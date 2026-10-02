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
