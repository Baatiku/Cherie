# Model runtime

OmniLive mounts a persistent Modal Volume named `omnilive-models` at `/models`. Large model files stay outside Git.

## Built-in effects

These need no external weights:
- original
- soft_focus
- monochrome
- pixelate

Built-in effects remain full-frame by default so transport can be verified independently of detector accuracy.

Set:

```text
OMNILIVE_LOCALIZED_EFFECTS=all
```

to route all non-original effects through the localized temporal pipeline.

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
1. TensorRTExecutionProvider
2. CUDAExecutionProvider
3. CPUExecutionProvider

CPU fallback remains intentional for local tests and CI.

The runtime now reports load time, input shape/type, active providers, process RSS, system memory, and GPU memory/name when `nvidia-smi` is available.

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

A future person detector has a reserved configurable path:

```text
OMNILIVE_PERSON_DETECTOR_MODEL=detectors/person_yolo.onnx
```

The person detector loader itself remains future work.

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
