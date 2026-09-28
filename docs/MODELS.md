# Model runtime

OmniLive mounts a persistent Modal Volume named `omnilive-models` at `/models`.

The built-in effects require no model files:
- original
- soft_focus
- monochrome
- pixelate

A generic ONNX visual-effect adapter is also available as `custom_onnx`.

Set:
```
OMNILIVE_MODEL_ID=my-effect/model.onnx
```

or place exactly one `.onnx` file in:
```
/models/my-effect/
```
and set:
```
OMNILIVE_MODEL_ID=my-effect
```

Provider priority:
1. TensorRT
2. CUDA
3. CPU fallback

The adapter accepts a single 4D image tensor and supports common NCHW/NHWC image-to-image models. Output is resized back to the incoming video frame so transport dimensions remain stable.

Model weights stay on the Modal Volume and are not committed to Git.
