# Model runtime

OmniLive mounts a persistent Modal Volume named `omnilive-models` at `/models`. Modal Volumes persist independently of an individual GPU container, so model files do not need to be downloaded again for every session.

## Built-in effects

These need no external weights:
- original
- soft_focus
- monochrome
- pixelate

## Generic ONNX adapter

The `custom_onnx` effect uses the configured image-to-image ONNX model.

Set:
```
OMNILIVE_MODEL_ID=my-effect/model.onnx
```

or put exactly one ONNX file under:
```
/models/my-effect/
```
and use:
```
OMNILIVE_MODEL_ID=my-effect
```

Provider preference:
1. TensorRT
2. CUDA
3. CPU

Configured models are preloaded when the Modal ASGI container starts so the first live frame does not pay model-load latency.

## Diagnostics

```
GET /v1/model
GET /v1/telemetry
POST /v1/benchmark
```

Example benchmark body:
```json
{
  "effect": "soft_focus",
  "width": 640,
  "height": 360,
  "frames": 60
}
```

Benchmarking runs synthetic frames through exactly the same processor/timing wrapper used by live WebRTC frames.
