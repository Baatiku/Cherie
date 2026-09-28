# Latency plan

## Rules

1. Never queue old camera frames.
2. Prefer the newest frame when overloaded.
3. Keep jitter buffer minimal.
4. Keep model state resident during a call.
5. Detect sparsely, track continuously.
6. Measure p50/p95 end-to-end frame age.

## Adaptive profiles

| Profile | Resolution | Camera FPS | Neural FPS | Detector cadence |
|---|---:|---:|---:|---:|
| low_latency | 640x360 | 20 | 10 | every 12 frames |
| balanced | 960x540 | 24 | 14 | every 8 frames |
| high | 1280x720 | 30 | 18 | every 6 frames |

Quality drops quickly when RTT, packet loss, backend p95, or encoder backlog rises, and recovers slowly to avoid oscillation.
