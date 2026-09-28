# Architecture

Physical camera -> Android WebRTC -> Modal GPU -> Android remote VideoTrack -> MediaCodec H.264 -> an Android abstract local socket -> LSPosed decoder inside Zoom -> Zoom Camera2 Surface.

The microphone is not proxied. Zoom continues to own its normal microphone path.

## Local bridge

Loading another WebRTC stack inside Zoom risks native-library collisions. The companion process owns cloud WebRTC. The hooked Zoom process only contains a local-socket client and Android MediaCodec decoder.

Protocol:
- greeting: ASCII OMNI1, int32 width, int32 height
- packet: uint8 type, int64 ptsUs, int32 byteLength, payload
- type 0 = AVC codec configuration
- type 1 = encoded frame

The relay binds only to 127.0.0.1.

## Camera2 virtualization

The module hooks framework CameraDeviceImpl capture-session creation and CaptureRequest.Builder addTarget/removeTarget.

For every output Surface:
- preserve the original for virtual video;
- create a dummy SurfaceTexture/Surface for the real camera session;
- replace the session output with the dummy;
- when Zoom later calls addTarget(original), substitute dummy;
- decode the localhost H.264 stream to the original Surface.

All hooks run in libxposed PROTECTIVE mode. Unsupported signatures fall through.

## Modal

The server uses HTTP signaling plus a long-lived GPU WebRTC peer. The first processor is an InsightFace-compatible ONNX swapper. Missing swap weights intentionally produce passthrough video for transport testing.
