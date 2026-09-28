# Rooted Android compatibility lab

The rootlab is compiled only when `-Pomnilive.rootLab=true`. Normal OmniLive builds do not depend on or execute this code.

## Current verified boundary

The rootlab-enabled APK compiles and links the following pieces:

- `CompatibilitySessionController`: package-scoped foreground/output/camera lifecycle state
- `CompatibilityRecoveryCoordinator`: bounded rebind retries, recovery state and physical-camera fallback
- `SuRootEnvironmentProbe`: verifies an actual root shell using `su -c id`
- `AndroidRecoveryScheduler`: main-loop delayed retry scheduling
- `RootLabProcessedFrameBridge`: forwards returned processed-frame metadata/output availability into the lab boundary
- `RootLabCompatibilityRuntime`: composes the above pieces for a concrete compatibility adapter
- `RootLabTargets.zoom()`: target preset for `us.zoom.videomeetings`
- `OmniLiveXposedModule`: Zoom-scoped libxposed Camera2 open/session/close hooks
- `RootLabSurfaceBrokerClient`: target-process Messenger client that parcels Zoom Camera2 output `Surface` objects back to OmniLive
- `RootLabSurfaceBrokerService`: rootlab-only exported broker that accepts messages only from the Zoom UID and binds returned-frame renderers to transferred surfaces
- `SharedEglContextRegistry`: generic stream-layer EGL handoff so optional outputs share the WebRTC decoder context safely

## Adapter contract

A device/package-specific implementation supplies `CameraCompatibilityAdapter`:

```kotlin
interface CameraCompatibilityAdapter : Closeable {
    fun attach(target: LabTarget): Boolean
    fun bindProcessedOutput(): Boolean
    fun restorePhysicalCamera(): Boolean
}
```

The adapter is deliberately below the transport/model path. It should only solve rooted camera redirection for the selected target package. WebRTC transport, Modal inference, reference-image handling and processed-frame generation remain unchanged.

The recovery coordinator does not infer that the target camera has reopened merely because a processed output rebind succeeded. A real target-camera lifecycle event must still arrive before the state can become `ACTIVE`.

## Recovery behavior

Default policy:

- up to 3 rebind attempts
- exponential retry delay starting at 350 ms, capped at 10 s
- after exhaustion, restore the physical camera
- if fallback is unavailable/disabled, enter `ERROR`
- moving the target to background cancels stale recovery work
- closing the runtime restores physical camera state and stops the session

## Zoom surface-handoff checkpoint

The first package-specific transport is now implemented. The Xposed module observes Zoom Camera2 capture-session outputs and forwards valid `Surface` objects over an explicit Messenger binding to the rootlab-only OmniLive broker. The broker waits for the active WebRTC EGL context and then attempts to create `SurfaceFrameOutput` renderers for every transferred target surface.

This phase is deliberately fail-open: the hooked Zoom Camera2 call still proceeds to the physical camera. That lets the rooted test device tell us whether the vendor/Zoom buffer queue allows a second producer attach before we add camera-session suppression/replacement logic.

The rootlab ADB smoke harness can now report:

- root shell availability
- broker component packaged in the APK
- Zoom package installed
- Xposed Camera2 hook inventory observed
- target surface attach observed

Use:

```bash
OMNILIVE_ROOTLAB_MODE=1 \
OMNILIVE_ROOTLAB_LAUNCH_TARGET=1 \
OMNILIVE_ROOTLAB_REQUIRE_HOOKS=1 \
bash ops/android-device-smoke.sh build-artifacts/omnilive-rootlab-debug.apk
```

## Remaining device-specific work

The remaining package-specific redirection step is no longer surface discovery/transport; it is physical-camera producer suppression or output replacement after real-device validation proves how Zoom/vendor Camera2 owns those transferred surfaces. Do not suppress the physical session until the smoke report confirms hook activation and whether the processed EGL producer can attach successfully.
