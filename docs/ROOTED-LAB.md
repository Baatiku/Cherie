# Rooted Android lab boundary

The rooted compatibility phase is isolated in the Android `:rootlab` library module.

This module is for personally controlled rooted-device camera plumbing tests. It deliberately does not contain:
- WebRTC signalling
- backend/model code
- target-app-specific hooks
- conferencing APK modification
- hidden altered-media bypass behavior

## Current boundary

The module now defines:
- validated package-scoped `LabTarget`
- explicit root-capability probe contract
- processed-output availability contract
- target foreground/background lifecycle
- target camera open/close lifecycle
- physical-camera fallback state
- recoverable vs fatal error state
- deterministic compatibility-session state machine

States:

```text
IDLE
  -> ATTACHING
  -> PAUSED / WAITING_FOR_OUTPUT / WAITING_FOR_CAMERA
  -> ACTIVE
  -> RECOVERING
  -> ACTIVE
  -> STOPPED

fatal capability/runtime failure -> ERROR
```

The state machine intentionally treats camera release/reopen and output loss as recoverable lifecycle events.

## Integration direction

Normal OmniLive remains root-free.

The integration seam is:

```text
Returned WebRTC frame
  -> ProcessedFrameOutput
  -> SurfaceFrameOutput
  -> rootlab bridge (future)
  -> authorized package-scoped compatibility adapter (future)
```

The future bridge should translate `ProcessedFrameInfo` into the buffer-neutral `LabFrameInfo` contract. Root capability and package-specific compatibility mechanics remain inside the lab module or a lab-only implementation module.

No root code should be introduced into:
- `backend/omnilive/processor.py`
- `backend/omnilive/webrtc.py`
- normal Android WebRTC transport
- model runtime
- session handling


## Lab-only app bridge

The normal app has no dependency on `:rootlab` by default.

Enable the optional lab source/dependency explicitly:

```bash
cd android
./gradlew :app:assembleDebug -Pomnilive.rootLab=true
```

The same property sets:

```text
BuildConfig.ROOT_LAB_ENABLED=true
```

Only that build compiles `app/src/rootlab/java` and adds `project(":rootlab")` to the app.

The current lab-only bridge translates:
- `ProcessedFrameInfo` -> `LabFrameInfo`
- processed-output availability -> the compatibility state machine
- processed-output availability -> the lab endpoint

Pixel rendering remains on `SurfaceFrameOutput`. The bridge does not intercept camera APIs or contain target-app-specific behavior.
