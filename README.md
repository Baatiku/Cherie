# OmniLive

OmniLive is a mobile-first low-latency AI camera pipeline for rooted Android test devices.

The phone owns the physical camera, streams encoded video to a GPU worker, receives the processed stream, and exposes the returned frames to an isolated local camera adapter used for development and compatibility testing.

## Goals

- Android-first: no laptop required during normal testing
- low latency: target <250 ms added round-trip latency
- GPU inference on Modal, not on the phone
- 720p/30 fps adaptive live mode
- session-affine GPU workers and warm containers
- face-region processing rather than full-frame generation
- automatic frame dropping when frames become stale
- no frame persistence by default

## Layout

- `android/` — Android companion application
- `backend/` — Modal/FastAPI realtime service
- `protocol/` — JSON contracts
- `docs/` — architecture, implementation ledger, latency plan

## Current milestone

Phase 1 foundation is implemented: Android app shell, foreground streaming service, Camera2 abstraction, adaptive quality controller, session registry, Modal L4 worker skeleton, latency/frame-age policy, health/session APIs, CI, and tests.

Next: production WebRTC transport and a GPU-resident FaceFusion-compatible processing adapter.
