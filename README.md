# OmniCam Live

Rooted-Android live virtual camera for the original Zoom app.

This repository replaces the former placeholder project in this repo. The previous single initial commit remains in Git history.

## What it does

1. The companion activity captures the physical front camera.
2. Standard WebRTC sends video to a Modal L4 worker.
3. The worker applies a pluggable face-swap transform and returns video.
4. Android re-encodes the returned frames as a low-latency local H.264 stream.
5. A libxposed/LSPosed module loaded inside the official Zoom app intercepts Camera2.
6. Zoom still believes it is using the normal camera, but its camera Surface receives the processed stream.

The project does not modify or redistribute the Zoom APK.

## Requirements

- Android 10+; Android 12+ recommended.
- Root via Magisk or KernelSU.
- LSPosed/Vector implementing libxposed API 102.
- Official Zoom Android app package: us.zoom.videomeetings.
- A Modal account for the GPU worker.
- JDK 17 and Android SDK 37 to build.
- A face-swap ONNX model that you have the right to use. Model weights are intentionally not committed.

## Build Android

Use Gradle 9.6.0+:

    gradle :app:assembleDebug

Install:

    adb install -r app/build/outputs/apk/debug/app-debug.apk

Then enable OmniCam Live in LSPosed and scope it only to Zoom.

## Deploy Modal

Create the API secret:

    modal secret create omnicam-config OMNICAM_API_TOKEN=replace-with-a-long-random-token

Create the model volume and place a compatible, legally obtained swap model at:

    /inswapper_128.onnx

Deploy:

    modal deploy backend/modal_app.py

Paste the resulting HTTPS endpoint and API token into the Android app.

## First test sequence

1. Start OmniCam Live and select a permitted/synthetic identity image.
2. Tap Start live camera.
3. Wait until the app reports WebRTC connected and local H.264 relay active.
4. Enable the module in LSPosed for Zoom and force-stop Zoom once.
5. Open the normal Zoom app and enter a meeting.
6. Turn on the Zoom camera.

If the swap model is not present, the Modal backend deliberately runs in passthrough mode so the transport and virtual-camera path can be tested independently.

See docs/ARCHITECTURE.md and docs/DEVICE_TESTING.md.

Use only your own likeness, synthetic identities, or identities for which you have permission. The backend adds an AI disclosure marker by default. This project does not include stealth or identity-verification bypass features.
