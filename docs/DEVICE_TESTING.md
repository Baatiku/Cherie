# Device testing checklist

Target first: rooted ARM64 Android 12-16.

Record:
- Android/vendor build
- root engine/version
- LSPosed/Vector version
- Zoom version
- Camera2 support
- H.264 codec names
- network type and RTT

Tests:
1. Companion holds the physical front camera while Zoom is open.
2. WebRTC reaches connected state.
3. Local H.264 relay gets a Zoom client.
4. Zoom receives virtual frames.
5. Portrait/landscape.
6. Toggle Zoom camera off/on repeatedly.
7. Background/foreground Zoom.
8. Modal disconnect must not crash Zoom.
9. End call releases decoder threads and dummy Surfaces.

Log filter:

    adb logcat -s OmniCam:*
