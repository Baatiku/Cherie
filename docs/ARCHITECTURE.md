# OmniLive architecture

## Live path

```
Android Camera2
  -> MediaCodec H.264
  -> realtime transport
  -> session-affine Modal worker
  -> decoder
  -> face detection (periodic)
  -> tracking (every frame)
  -> face processor
  -> GPU composite
  -> encoder
  -> realtime transport
  -> Android MediaCodec decoder
  -> local output Surface
```

## Latency strategy

- one warm worker per active session during benchmarking
- sticky session affinity
- drop stale frames instead of buffering
- process face crop, not full frame
- detector cadence lower than render cadence
- reuse tracker state
- hardware encode/decode
- adaptive resolution and fps

Target added latency is <250 ms, with a stretch goal below 180 ms on a good network.

## Session model

Each session has a random id, short-lived bearer token, quality profile, expiry, worker-affinity key, latency telemetry, and monotonic frame sequence numbers.

The backend stores no video frames.
