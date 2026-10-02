# Model runtime

OmniLive mounts a persistent Modal Volume named `omnilive-models` at `/models`. Large model files stay outside Git.

OmniLive does one thing: face swap. The only effects are:

- original (untouched pass-through, used as the bypass)
- custom_onnx (the face-swap model, localized by default)

There are no other visual styles. The app always runs face swap.

## Real-time identity face swap

The `face_swap` effect is a dedicated identity pipeline, not a generic
filter. It uses the InsightFace inswapper family (128px, fp16) with an
ArcFace identity vector computed once from the session reference photo:

```text
/models/detectors/det_10g.onnx
/models/recognition/w600k_r50.onnx
/models/swapper/inswapper_128_fp16.onnx
```

```text
OMNILIVE_FACE_DETECTOR_MODEL=detectors/det_10g.onnx
OMNILIVE_SWAPPER_MODEL=swapper/inswapper_128_fp16.onnx
OMNILIVE_ARCFACE_MODEL=recognition/w600k_r50.onnx
```

How it works per live frame:

1. the detector finds the face and its 5 keypoints; between full scans
   it re-detects inside a small region around the last face every frame,
   and the points pass through a low-lag smoothing filter
2. the face is aligned to the ArcFace template (exact least-squares,
   no RANSAC, so the mouth cannot land twice)
3. the swapper paints the reference identity onto the aligned crop
   (about 5ms on the L4), then an enhancer sharpens it and the crop
   takes on the reference photo's skin tone
4. the full-strength swapped face is warped back into the frame inside a
   face-shaped oval mask, with hands and glasses kept real by an
   occlusion mask — no square borders, and masking never eats the
   identity itself

Between swap slots the last swapped crop is re-pasted at the current
face position, so output stays at camera frame rate. If the face is
briefly lost, the last swapped frame freezes instead of flashing the
real face; raw fall-through is counted as a safety bug. A reference photo
with a clear detectable face is required; without one the frame passes
through untouched. Provision the weights with:

```text
cd backend
python -m modal run scripts/provision_swap_models.py
```

## Generic ONNX visual adapter

The `custom_onnx` effect is localized by default and uses the configured image-to-image ONNX model.

Direct file:

```text
OMNILIVE_MODEL_ID=my-effect/model.onnx
```

or directory containing exactly one ONNX file:

```text
/models/my-effect/
OMNILIVE_MODEL_ID=my-effect
```

Provider preference is:
1. CUDAExecutionProvider
2. CPUExecutionProvider

TensorRT is deliberately not listed: when its system libraries are absent,
merely including it makes ONNX Runtime skip CUDA as well and fall back to
CPU, which is ~100x too slow for live swap. Only add it back on images
that actually install TensorRT.

A CPU-only environment such as local development or Render CI still loads CPUExecutionProvider normally. On a GPU worker, execution-provider failures are treated separately from model/input errors: a failing TensorRT/CUDA provider is temporarily quarantined, the ONNX session is discarded, and OmniLive immediately tries the next healthy configured provider. CPU fallback after a GPU-provider failure is opt-in so production does not silently hide a broken GPU path.

Recovery controls:

```text
OMNILIVE_MODEL_ALLOW_CPU_FALLBACK=false
OMNILIVE_MODEL_RETRY_COOLDOWN_SECONDS=1.5
OMNILIVE_MODEL_PROVIDER_QUARANTINE_SECONDS=30
```

If the alternate-provider recovery also fails, the runtime enters the retry cooldown. During that cooldown the live processor does not hammer ONNX on every neural slot: it reuses a still-valid cached transform when possible and otherwise returns the untouched frame.

Runtime status/telemetry exposes the active providers, quarantined providers, execution-failure count, recovery count, consecutive failures, cooldown remaining, recovery state, load time, input shape/type, process RSS, system memory, and GPU memory/name when `nvidia-smi` is available.

## Face detector and landmark providers

The zero-download Haar detector remains the fallback, but the processor now has a lazy GPU-capable SCRFD ONNX provider and a semantic landmark provider.

Default expected Volume paths:

```text
/models/detectors/scrfd_2.5g.onnx
/models/landmarks/face_landmark_5.onnx
```

Configuration:

```text
OMNILIVE_DETECTOR_BACKEND=auto
OMNILIVE_FACE_DETECTOR_MODEL=detectors/scrfd_2.5g.onnx
OMNILIVE_FACE_DETECTOR_CONFIDENCE=0.50
OMNILIVE_FACE_DETECTOR_NMS=0.40

OMNILIVE_LANDMARK_BACKEND=auto
OMNILIVE_FACE_LANDMARK_MODEL=landmarks/face_landmark_5.onnx
OMNILIVE_FACE_ALIGNMENT_ENABLED=false
OMNILIVE_FACE_ALIGNMENT_SIZE=256
```

`auto` uses the ONNX provider when its configured file exists and otherwise keeps the Haar/sparse-tracker baseline working. Model paths are restricted to `/models`.

The landmark adapter accepts common 5/68/106-point ONNX outputs. Semantic landmarks are refreshed on detector passes and moved between passes with the sparse tracker's affine transform.

When face alignment is enabled for `custom_onnx`, OmniLive:
1. aligns only the localized face region
2. runs the same custom model adapter on that aligned crop
3. warps the transformed crop back into the tracked region
4. applies the existing color match and feathered compositor

No full-frame neural inference is introduced.

The person detector provider uses a separately configured ONNX model:

```text
OMNILIVE_PERSON_DETECTOR_BACKEND=auto
OMNILIVE_PERSON_DETECTOR_MODEL=detectors/person_yolo.onnx
OMNILIVE_PERSON_DETECTOR_CONFIDENCE=0.40
OMNILIVE_PERSON_DETECTOR_NMS=0.45
```

It supports common Ultralytics feature-first/row-first raw outputs and end-to-end `[x1,y1,x2,y2,score,class]` outputs. Preprocessing uses letterboxing and postprocessing applies person-class filtering plus NMS. The provider is lazy and does not load weights unless person detection is actually invoked.

## Localized temporal pipeline

Current pipeline:
- replaceable detector interface
- lazy SCRFD ONNX face detector with TensorRT/CUDA/CPU preference
- OpenCV Haar fallback
- semantic face landmark provider
- optional face alignment
- sparse Lucas-Kanade feature tracking between detector passes
- detector refresh cadence controlled by quality/load
- detector downscale cap
- padded/clamped tracked crop
- dynamic crop-size cap
- independent neural inference schedule
- short successful-transform cache
- confidence-based cache invalidation
- custom-model color/illumination matching
- feathered composite back into the original frame

## Tuning variables

```text
OMNILIVE_DETECTOR_MAX_SIDE=640
OMNILIVE_REGION_PADDING=0.22
OMNILIVE_TRACKER_MIN_CONFIDENCE=0.22
OMNILIVE_PROCESSOR_CONTEXT_TTL_SECONDS=30
OMNILIVE_LOCALIZED_EFFECTS=custom_onnx
```

## Real-pipeline benchmark

The single-run compatibility endpoint remains:

```text
POST /v1/benchmark
```

The real model benchmark matrix is:

```text
POST /v1/benchmark/matrix
```

Example:

```json
{
  "effect": "custom_onnx",
  "profiles": ["low_latency", "balanced", "high"],
  "crop_sizes": [256, 320, 384, 448],
  "width": 960,
  "height": 540,
  "warmup_frames": 2,
  "steady_frames": 30,
  "force_region": true,
  "include_adaptation_probe": true,
  "adaptation_frames": 24
}
```

Every matrix case runs through the same `TimedProcessor -> LocalizedTemporalProcessor -> VisualProcessor -> OnnxRuntime` path used by live WebRTC. The benchmark does not call a separate fake inference implementation.

For each fixed crop size it reports:
- runtime/provider and whether the runtime was already warm
- model load time
- first model and full-pipeline latency
- warmup timings
- steady-state model p50/p95
- steady-state full-pipeline p50/p95
- actual model input dimensions
- p95-derived sustainable neural/pipeline FPS
- inference success/error counts
- final adaptive-budget state
- process/system/GPU memory where accessible

The fixed crop matrix deliberately holds the requested crop cap for comparable 256/320/384/448 measurements.

The separate adaptation probe does not hold the crop cap. It force-stresses the same live processor and reports whether overload caused:
- neural FPS reduction
- detector cadence increase
- crop-cap reduction

A Modal preload means `runtime_loaded_before=true` is expected in a warm L4 container. `runtime_load_ms` still records the model session load cost from container startup. A genuinely cold-start benchmark must also record Modal container startup time externally.


## Session reference image

Android can select one reference image before starting a live session. The app normalizes it locally to a bounded JPEG and sends it once over the already-pinned `/v1/live` WebSocket after the SDP answer.

The backend:
- validates base64 and decoded image data
- rejects oversized or tiny inputs
- caps the longest side to 1024 px
- normalizes the image to JPEG
- stores it only in the current in-memory pinned session
- returns width, height and SHA-256 metadata to Android

This deliberately avoids a separate HTTP upload, because an unrelated Modal HTTP request could land on a different GPU container from the pinned live WebSocket.

The default remains:

```text
OMNILIVE_MODEL_ADAPTER=single_image
```

so existing one-input models ignore the reference image and behave exactly as before.

For compatible ONNX models with exactly two image inputs, enable:

```text
OMNILIVE_MODEL_ADAPTER=dual_image
OMNILIVE_MODEL_LIVE_INPUT=
OMNILIVE_MODEL_REFERENCE_INPUT=
```

When input names are blank, the first ONNX input is treated as the live crop and the second as the reference. Set the names explicitly when a model exports them in another order. Both inputs must be 4D image tensors (NCHW or NHWC); each is resized independently to its declared input geometry.

The live processor decodes the session JPEG once per reference SHA, caches the RGB reference per stream, invalidates the transformed-crop cache when the reference changes, and supplies it only to `custom_onnx`. If a `dual_image` model is selected before a reference arrives, the processor returns the untouched live frame instead of recording repeated inference failures.

Models that require embeddings, latent vectors, masks, or more than two inputs should get a dedicated adapter rather than being forced into this generic two-image contract.
