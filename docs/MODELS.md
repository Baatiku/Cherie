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

## Generic ONNX adapter

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

Provider preference:
1. TensorRTExecutionProvider
2. CUDAExecutionProvider
3. CPUExecutionProvider

CPU fallback remains intentional for local tests and CI.

## Localized temporal pipeline

Current baseline:
- replaceable `RegionDetector` interface
- bundled OpenCV Haar face detector as a zero-download baseline
- sparse Lucas-Kanade feature/landmark tracking
- detector refresh cadence controlled by quality/load
- detector downscale cap
- padded/clamped tracked crop
- dynamic crop-size cap
- independent neural inference schedule
- short successful-transform cache
- confidence-based cache invalidation
- custom-model color/illumination matching
- feathered composite back into the original frame

The Haar detector is not the final production detector. It exists so the full low-latency architecture can be exercised before external detector weights are selected.

## Tuning variables

```text
OMNILIVE_DETECTOR_MAX_SIDE=640
OMNILIVE_REGION_PADDING=0.22
OMNILIVE_TRACKER_MIN_CONFIDENCE=0.22
OMNILIVE_PROCESSOR_CONTEXT_TTL_SECONDS=30
OMNILIVE_LOCALIZED_EFFECTS=custom_onnx
```

## Diagnostics

```text
GET /v1/model
GET /v1/telemetry
POST /v1/benchmark
```

Example:

```json
{
  "effect": "custom_onnx",
  "profile": "balanced",
  "width": 640,
  "height": 360,
  "frames": 60,
  "force_region": true,
  "respect_inference_budget": false
}
```

`force_region=true` makes a synthetic benchmark exercise the crop/model/compositor path even though the generated frame does not contain a real face.

`respect_inference_budget=false` forces every benchmark frame through inference so model latency is measurable. Live WebRTC uses the adaptive inference budget instead.
