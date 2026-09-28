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

`input_age_p50_ms` and `input_age_p95_ms` measure decoded-frame arrival to processor pickup. They remain useful for diagnosing backend backlog, but they are distinct from the frame-correlated round-trip metric.

## Frame-correlated round-trip timing

Video remains entirely on WebRTC. The live WebSocket transports only compact timing metadata.

For each marked capture:
1. Android assigns a monotonic sequence.
2. Android records the camera frame timestamp and `SystemClock.elapsedRealtimeNanos()`.
3. Android sends a 90 kHz RTP timestamp estimate with the sequence.
4. The backend calibrates that estimate against actual decoded aiortc frame PTS/RTP timing.
5. The backend records decoded receive, processor start, and processor end times.
6. The returned frame preserves the input frame PTS.
7. Android calibrates the returned decoder timestamp to the backend report and matches the returned frame to the original sequence.
8. Android computes p50/p95 from local capture time to returned-video sink / renderer-input arrival.

This does not add pixels, frame copies, or a media queue to the signalling path. Both correlation stores and the backend timing publisher are bounded. Under overload, correlation metadata is discarded and loss counters increase rather than delaying video.

The UI exposes:
- frame round-trip p50/p95
- correlated sample count
- correlation misses
- out-of-order timing reports
- backend receive-to-processed-frame time for the latest matched frame

A future renderer-present callback can refine the final display-present boundary. The current number is capture-to-returned-renderer-input and must not be described as physical display scanout latency.

## Adaptive profiles

| Profile | Resolution | Camera FPS | Neural FPS | Detector cadence | Initial crop cap | Sender max bitrate |
|---|---:|---:|---:|---:|---:|---:|
| low_latency | 640x360 | 20 | 10 | every 12 frames | 256 px | 700 kbps |
| balanced | 960x540 | 24 | 14 | every 8 frames | 384 px | 1400 kbps |
| high | 1280x720 | 30 | 18 | every 6 frames | 448 px | 2500 kbps |

Android quality drops quickly when RTT, packet loss, or backend processor p95 worsens and recovers more slowly to avoid oscillation.

The same profile now controls:
- capture resolution/FPS
- sender maximum bitrate and sender maximum framerate
- backend neural inference budget through the pinned WebSocket profile update

There is still only one quality hysteresis loop. Bitrate control does not independently oscillate against capture adaptation.

The backend has an independent neural budget. Consecutive model overruns:
- reduce target neural FPS
- increase detector interval
- reduce crop maximum side

Recovery requires a longer healthy streak.

## Temporal reuse

Between inference slots the tracker continues to update the region on camera frames. The most recent successful transformed crop may be re-composited at the updated region only while:
- cache TTL is valid
- the tracker still has a region
- tracker confidence is above threshold

A confidence collapse clears the transform cache.

## Transport telemetry

Android WebRTC stats now expose:
- nominated local/remote ICE candidate types
- candidate transport protocol
- relay/direct state
- current candidate-pair RTT
- packet loss
- actual video send/receive kbps from byte deltas
- available outgoing bitrate when the implementation reports it
- target bitrate tier
- encoder FPS
- encoded/sent frame counts

Candidate addresses are not required for the benchmark UI.

## Processor telemetry

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
- frame-correlation matches/drops/out-of-order metadata
- current detector interval
- current crop cap
- target neural FPS
- tracking confidence
- active runtime/model provider diagnostics
