# Latency plan

## Rules

1. Never queue old camera frames.
2. Prefer the newest frame when overloaded.
3. Keep model state resident during a live call.
4. Detect sparsely and track continuously.
5. Run neural inference on its own FPS budget.
6. Reuse transformed content only while geometry and confidence are trustworthy.
7. Measure p50/p95 per processing stage.
8. Degrade neural work before sacrificing camera cadence.

## Latest-frame input policy

The backend continuously drains the incoming aiortc video track into a one-frame latest slot.

If inference/compositing has not consumed the previous frame when a new decoded frame arrives, the old frame is overwritten and `input_overwritten_frames` increments. There is no unbounded processor queue.

`input_age_p50_ms` and `input_age_p95_ms` measure decoded-frame arrival to processor pickup. This is useful for detecting backend backlog, but it is not yet the final capture-to-render round-trip metric.

## Adaptive profiles

| Profile | Resolution | Camera FPS | Neural FPS | Detector cadence | Initial crop cap |
|---|---:|---:|---:|---:|---:|
| low_latency | 640x360 | 20 | 10 | every 12 frames | 256 px |
| balanced | 960x540 | 24 | 14 | every 8 frames | 384 px |
| high | 1280x720 | 30 | 18 | every 6 frames | 448 px |

Android quality drops quickly when RTT, packet loss, or backend processor p95 worsens and recovers more slowly to avoid oscillation.

The backend has an independent neural budget. Consecutive model overruns:
- reduce target neural FPS
- increase detector interval
- reduce crop maximum side

Recovery requires a longer healthy streak.

Adaptive Android profile changes are sent over the pinned live WebSocket so backend model budgeting follows the real capture profile instead of the profile that happened to start the call.

## Temporal reuse

Between inference slots the tracker continues to update the region on camera frames. The most recent successful transformed crop may be re-composited at the updated region only while:
- cache TTL is valid
- the tracker still has a region
- tracker confidence is above threshold

A confidence collapse clears the transform cache.

## Stage telemetry

Worker-local live telemetry reports p50/p95 for:
- input wait
- input age
- detect
- track
- model
- composite
- total processor latency

It also exposes:
- dropped inference frames
- overwritten decoded input frames
- transform cache reuse
- tracking resets
- inference errors
- overload events
- current detector interval
- current crop cap
- target neural FPS
- tracking confidence
- active runtime/model provider diagnostics

## Remaining end-to-end timing work

Precise capture-to-render latency still needs a frame-correlated timestamp path across both peers. Do not approximate this from network RTT plus processor time and call it end-to-end latency.

Until that instrumentation exists, use RTT, input age, processor stage timing, and Android rendered/received frame counts as separate measurements.
