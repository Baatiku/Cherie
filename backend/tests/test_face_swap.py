from __future__ import annotations

import math
import numpy as np
import pytest

from omnilive.face_swap import (
    ENGINES,
    _OneEuro,
    _decompose_similarity,
    _face_oval,
    _match_target_skin,
    _recompose_similarity,
    _similarity,
    _soft_box_mask,
    _template_points,
    norm_crop,
    paste_back,
)


def test_engine_specifications_normalization():
    """Verify input normalization and output denormalization contracts."""
    # Inswapper: uint8 [0, 255] -> [0.0, 1.0] -> [0.0, 255.0]
    inswap = ENGINES["inswapper"]
    assert inswap["crop"] == 128
    assert math.isclose(inswap["input_scale"], 1.0 / 255.0)
    assert inswap["input_mean"] == (0.0, 0.0, 0.0)
    assert inswap["denorm_mult"] == 255.0
    assert inswap["denorm_add"] == 0.0

    # HyperSwap: uint8 [0, 255] -> [-1.0, 1.0] -> [0.0, 255.0]
    for key in ("hyper1a", "hyper1b", "hyper1c"):
        spec = ENGINES[key]
        assert spec["crop"] == 256
        assert math.isclose(spec["input_scale"], 1.0 / 127.5)
        assert spec["input_mean"] == (127.5, 127.5, 127.5)
        # Verify input mapping
        test_val = 255.0
        normalized_in = (test_val - spec["input_mean"][0]) * spec["input_scale"]
        assert math.isclose(normalized_in, 1.0, rel_tol=1e-5)
        # Verify output denormalization (must NOT collapse to flat gray)
        model_out = 1.0
        denorm_val = model_out * spec["denorm_mult"] + spec["denorm_add"]
        assert math.isclose(denorm_val, 255.0, rel_tol=1e-5)

        model_out_min = -1.0
        denorm_val_min = model_out_min * spec["denorm_mult"] + spec["denorm_add"]
        assert math.isclose(denorm_val_min, 0.0, abs_tol=1e-5)


def test_one_euro_filter_dynamic_dt():
    """OneEuro filter must smooth signals with variable timestamp intervals."""
    euro = _OneEuro(freq=30.0, min_cutoff=1.0, beta=0.01)
    val0 = euro(100.0, timestamp=0.0)
    assert val0 == 100.0

    # Add noisy step with dynamic dt
    val1 = euro(110.0, timestamp=0.033)
    assert 100.0 < val1 < 110.0

    # Larger interval dt should yield greater responsiveness
    euro2 = _OneEuro(freq=30.0, min_cutoff=1.0, beta=0.01)
    euro2(100.0, timestamp=0.0)
    val2 = euro2(110.0, timestamp=0.200)
    assert val2 > val1


def test_similarity_rigid_decomposition_recomposition():
    """Decomposing and recomposing similarity matrix must be an exact identity operation."""
    tx, ty = 45.2, -18.7
    scale = 1.34
    angle = 0.28  # ~16 degrees

    matrix = _recompose_similarity(tx, ty, scale, angle)
    dec_tx, dec_ty, dec_scale, dec_angle = _decompose_similarity(matrix)

    assert math.isclose(dec_tx, tx, rel_tol=1e-4)
    assert math.isclose(dec_ty, ty, rel_tol=1e-4)
    assert math.isclose(dec_scale, scale, rel_tol=1e-4)
    assert math.isclose(dec_angle, angle, rel_tol=1e-4)


def test_yaw_robust_similarity():
    """Under horizontal foreshortening (yaw), vertical facial baseline must stabilize scale."""
    dst = _template_points("arcface_112", 112)
    # Perfectly frontal face matching template
    src_frontal = dst.copy()
    mat_frontal = _similarity(src_frontal, dst)
    assert mat_frontal is not None
    _, _, scale_frontal, _ = _decompose_similarity(mat_frontal)
    assert math.isclose(scale_frontal, 1.0, rel_tol=1e-3)

    # Simulated head turn (yaw): eyes move closer horizontally, but vertical baseline is preserved
    src_yaw = src_frontal.copy()
    mid_x = (src_yaw[0, 0] + src_yaw[1, 0]) * 0.5
    src_yaw[0, 0] = mid_x - (mid_x - src_yaw[0, 0]) * 0.5  # 50% horizontal compression
    src_yaw[1, 0] = mid_x + (src_yaw[1, 0] - mid_x) * 0.5

    mat_yaw = _similarity(src_yaw, dst)
    assert mat_yaw is not None
    _, _, scale_yaw, _ = _decompose_similarity(mat_yaw)
    # Scale must remain stable and not collapse
    assert scale_yaw >= 0.85


def test_soft_box_mask_and_anatomical_oval():
    """Mask must be normalized [0, 1] with soft feathered borders."""
    mask = _face_oval(128, "arcface_128")
    assert mask.shape == (128, 128)
    assert mask.min() >= 0.0
    assert mask.max() <= 1.0
    # Core interior should be strong
    assert mask[64, 64] > 0.8
    # Edges should be 0.0 due to soft border decay
    assert mask[0, 0] == 0.0
    assert mask[0, 64] == 0.0
    assert mask[127, 127] == 0.0


def test_match_target_skin_chrominance_only():
    """L channel must NOT be darkened to match a dark-skin target; only colour cast (a/b) is transferred.

    Scenario: reference identity outputs a bright face (high L); live user has dark skin (low L).
    Before fix: Reinhard on L pulled brightness down → eyes invisible.
    After fix: L shift is clamped to ±12 LAB units; the bright model output stays bright.
    """
    import cv2

    # Simulated swapped face: model output at ~reference skin luminance (light brown)
    # Approximately BGR [200, 160, 130] — a typical lighter brown skin in RGB
    swapped = np.full((128, 128, 3), [200, 160, 130], dtype=np.uint8)
    swapped_lab = cv2.cvtColor(swapped, cv2.COLOR_RGB2LAB).astype(np.float32)
    swapped_L_mean = float(swapped_lab[..., 0].mean())

    # Live target face: dark skin, much lower luminance (RGB ~[80, 55, 40])
    target = np.full((128, 128, 3), [80, 55, 40], dtype=np.uint8)

    matched = _match_target_skin(swapped, target, amount=1.0)
    assert matched.shape == (128, 128, 3)
    assert matched.dtype == np.uint8

    matched_lab = cv2.cvtColor(matched, cv2.COLOR_RGB2LAB).astype(np.float32)
    matched_L_mean = float(matched_lab[..., 0].mean())

    # Core assertion: luminance drop must be ≤ 12 LAB units (our clamp).
    # Before fix this would drop ~30-40 units, making eyes invisible on dark skin.
    L_drop = swapped_L_mean - matched_L_mean
    assert L_drop <= 13.0, (
        f"Luminance dropped by {L_drop:.1f} LAB units — skin darkening bug not fixed "
        f"(swapped L={swapped_L_mean:.1f}, matched L={matched_L_mean:.1f})"
    )

    # Secondary: the overall output must still be reasonable (not clamped to black)
    assert matched.mean() > 50.0


def test_match_target_skin_ab_channels_transferred():
    """Chrominance (a, b channels) must be transferred from target to correct colour cast."""
    import cv2

    # Swapped face: neutral grey (no colour cast)
    swapped = np.full((128, 128, 3), [150, 150, 150], dtype=np.uint8)
    # Target: warm orange-tinted skin (positive a = red-green shift)
    target = np.full((128, 128, 3), [180, 120, 90], dtype=np.uint8)

    matched = _match_target_skin(swapped, target, amount=1.0)

    swapped_lab = cv2.cvtColor(swapped, cv2.COLOR_RGB2LAB).astype(np.float32)
    target_lab = cv2.cvtColor(target, cv2.COLOR_RGB2LAB).astype(np.float32)
    matched_lab = cv2.cvtColor(matched, cv2.COLOR_RGB2LAB).astype(np.float32)

    # The a channel of matched must be closer to target than to swapped
    swapped_a = float(swapped_lab[..., 1].mean())
    target_a = float(target_lab[..., 1].mean())
    matched_a = float(matched_lab[..., 1].mean())

    dist_to_target = abs(matched_a - target_a)
    dist_to_swapped = abs(matched_a - swapped_a)
    assert dist_to_target < dist_to_swapped, (
        f"a-channel not transferred toward target: "
        f"swapped_a={swapped_a:.1f}, target_a={target_a:.1f}, matched_a={matched_a:.1f}"
    )



def test_paste_back_blending_without_core_override():
    """Paste back must preserve occlusion masks (hands/glasses) without forced core clamp."""
    frame = np.full((256, 256, 3), 50, dtype=np.uint8)
    swapped = np.full((128, 128, 3), 200, dtype=np.uint8)
    transform = np.array([[1.0, 0.0, 64.0], [0.0, 1.0, 64.0]], dtype=np.float32)

    # Occluder mask covering the center of the face (e.g. finger or hand)
    extra_mask = np.ones((128, 128), dtype=np.float32)
    extra_mask[50:80, 50:80] = 0.0  # hand occluding center

    pasted = paste_back(frame, swapped, transform, extra_masks=[extra_mask], template="arcface_128")
    assert pasted.shape == (256, 256, 3)
    assert pasted.dtype == np.uint8

    # The occluded center (64+64 = 128) must preserve target frame pixels (near 50), not swapped (200)
    center_val = pasted[128, 128, 0]
    assert center_val < 100  # verified that occluder was NOT overridden by core


def test_paste_back_opaque_face_interior():
    """Face interior (mouth, eyes, nose) must be 100% opaque without 15% target bleed."""
    frame = np.full((256, 256, 3), 30, dtype=np.uint8)     # dark background/target
    swapped = np.full((128, 128, 3), 220, dtype=np.uint8)  # bright swapped face
    # transform maps frame (256x256) -> crop (128x128): crop = frame - 64
    transform = np.array([[1.0, 0.0, -64.0], [0.0, 1.0, -64.0]], dtype=np.float32)

    pasted = paste_back(frame, swapped, transform, extra_masks=None, template="arcface_128")
    assert pasted.shape == (256, 256, 3)

    # In the center of the face (x=128, y=128), opacity must be 1.0 (pixel value == 220, not 220*0.85 + 30*0.15 = 191)
    center_val = pasted[128, 128, 0]
    assert center_val == 220, f"Expected 220 full opacity in face center, got {center_val}"

    # In the mouth region (center x=128, mouth y=64 + 128*0.72 = 156), opacity must also be full 220
    mouth_val = pasted[156, 128, 0]
    assert mouth_val >= 215, f"Expected >=215 opacity in mouth center, got {mouth_val}"



def test_vertical_jaw_drop_and_mouth_dynamics():
    """Vertical jaw drop must trigger speech/mouth opening detection."""
    from omnilive.face_swap import _ARCFACE_128, SwapStream

    context = SwapStream(profile_name="balanced")
    # Base neutral face landmarks
    neutral_kps = (_ARCFACE_128 * 256.0).copy()

    # Calculate v_jaw for neutral face
    eye_dist = float(np.linalg.norm(neutral_kps[1] - neutral_kps[0])) + 1e-6
    mouth_center = (neutral_kps[3] + neutral_kps[4]) * 0.5
    nose = neutral_kps[2]
    eye_center = (neutral_kps[0] + neutral_kps[1]) * 0.5
    face_v = mouth_center - eye_center
    face_axis = face_v / (float(np.linalg.norm(face_v)) + 1e-6)
    v_jaw_neutral = float(np.dot(mouth_center - nose, face_axis)) / eye_dist

    context.baseline_v_jaw = v_jaw_neutral
    context.cached_v_jaw = v_jaw_neutral

    # 1. Closed mouth: jaw drop is near zero
    jaw_drop = v_jaw_neutral - context.baseline_v_jaw
    assert jaw_drop < 0.01

    # 2. Open mouth / jaw drop: mouth corners drop downward by 15 pixels
    open_kps = neutral_kps.copy()
    open_kps[3, 1] += 15.0
    open_kps[4, 1] += 15.0

    open_mouth_center = (open_kps[3] + open_kps[4]) * 0.5
    open_face_v = open_mouth_center - eye_center
    open_face_axis = open_face_v / (float(np.linalg.norm(open_face_v)) + 1e-6)
    v_jaw_open = float(np.dot(open_mouth_center - nose, open_face_axis)) / eye_dist

    open_jaw_drop = v_jaw_open - context.baseline_v_jaw
    # Jaw drop must be significant (> 0.035 threshold)
    assert open_jaw_drop > 0.035, f"Expected jaw drop > 0.035, got {open_jaw_drop:.4f}"


def test_face_swap_parser_status_and_fallback():
    """FaceSwapEngine exposes parser status and falls back gracefully when model absent."""
    from omnilive.face_swap import FaceSwapEngine

    engine = FaceSwapEngine()
    assert hasattr(engine, "parser")
    assert hasattr(engine, "parser_enabled")
    status = engine.status()
    assert "parser_model" in status
    assert "parser_enabled" in status

    # Without the parser loaded/present, _blend_inner_mouth returns swapped untouched
    swapped = np.full((256, 256, 3), 200, dtype=np.uint8)
    aligned = np.full((256, 256, 3), 50, dtype=np.uint8)
    res = engine._blend_inner_mouth(swapped, aligned, 256)
    assert np.array_equal(res, swapped)


def test_blend_inner_mouth_passthrough():
    """Inner mouth pass-through blends speaker teeth into swapped face inside mouth cavity."""
    from unittest.mock import MagicMock
    from omnilive.face_swap import FaceSwapEngine

    engine = FaceSwapEngine()
    swapped = np.full((256, 256, 3), 200, dtype=np.uint8)  # bright swapped face
    aligned = np.full((256, 256, 3), 50, dtype=np.uint8)   # dark speaker teeth/mouth

    # Mock parser session returning 19-class logits with class 11 (inner mouth) in the mouth center
    mock_session = MagicMock()
    mock_input = MagicMock()
    mock_input.name = "input"
    mock_session.get_inputs.return_value = [mock_input]

    # Create synthetic logits (1, 19, 512, 512) where class 0 is default (logits=10.0)
    # and class 11 (inner mouth) has highest logit in mouth region (y: 350..400, x: 220..300)
    logits = np.full((1, 19, 512, 512), -10.0, dtype=np.float32)
    logits[0, 0, :, :] = 5.0
    logits[0, 11, 360:390, 230:280] = 20.0  # open oral cavity with teeth
    mock_session.run.return_value = [logits]

    # Temporarily set mock session and enable parser
    engine.parser._session = mock_session
    original_configured = engine.parser.configured_file_exists
    try:
        type(engine.parser).configured_file_exists = property(lambda self: True)
        blended = engine._blend_inner_mouth(swapped, aligned, 256)
        assert blended.shape == (256, 256, 3)

        # Cheeks and forehead (e.g. y=100, x=128) must remain 100% swapped (200)
        assert blended[100, 128, 0] == 200

        # Mouth interior (mapped from 512 -> 256: y ~ 185, x ~ 128) must show speaker pixels (near 50)
        mouth_pixel = blended[187, 128, 0]
        assert mouth_pixel < 80, f"Expected teeth pass-through (<80), got {mouth_pixel}"
    finally:
        engine.parser._session = None
        type(engine.parser).configured_file_exists = original_configured


def test_norm_crop_expression_decoupling_stability():
    """Mouth opening / jaw drop must not shrink, squash, or shift skull scale."""
    from omnilive.face_swap import _ARCFACE_128, _template_points, norm_crop, _decompose_similarity

    frame = np.full((300, 300, 3), 128, dtype=np.uint8)
    dst = _template_points("arcface_128", 256)

    # 1. Neutral face landmarks
    neutral_kps = dst.copy()
    _, t_neutral = norm_crop(frame, neutral_kps, 256, "arcface_128")
    assert t_neutral is not None
    _, _, scale_neutral, _ = _decompose_similarity(t_neutral)

    # 2. Open mouth / speech: jaw drops by 25 pixels
    open_kps = dst.copy()
    open_kps[3, 1] += 25.0
    open_kps[4, 1] += 25.0
    _, t_open = norm_crop(frame, open_kps, 256, "arcface_128")
    assert t_open is not None
    _, _, scale_open, _ = _decompose_similarity(t_open)

    # Scale must remain stable (within 0.5%, not the 18% shrink of raw 5-point Umeyama)
    scale_error = abs(scale_open - scale_neutral)
    assert scale_error < 0.005, f"Expected scale error < 0.005, got {scale_error:.4f}"


