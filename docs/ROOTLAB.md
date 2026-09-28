# Rooted Android compatibility lab

The rootlab is compiled only when `-Pomnilive.rootLab=true`. Normal OmniLive builds do not depend on, package, or execute this code.

## Current verified boundary

The rootlab-enabled APK compiles and links:

- `CompatibilitySessionController`: package-scoped foreground/output/camera lifecycle state
- `CompatibilityRecoveryCoordinator`: bounded rebind retries, recovery state and physical-camera fallback
- `SuRootEnvironmentProbe`: verifies an actual root shell using `su -c id`
- `AndroidRecoveryScheduler`: main-loop delayed retry scheduling
- `RootLabProcessedFrameBridge`: forwards returned processed-frame availability into the lab boundary
- `RootLabCompatibilityRuntime`: composes lifecycle, recovery and frame availability for the selected target
- `RootLabTargets.zoom()`: target preset for `us.zoom.videomeetings`
- `OmniLiveXposedModule`: Zoom-scoped libxposed Camera2 lifecycle/request/session hooks
- `RootLabSurfaceBrokerClient`: Zoom-process Messenger client
- `RootLabSurfaceBrokerService`: rootlab-only OmniLive broker that accepts messages only from the Zoom UID
- `TargetSurfaceRedirector`: coupled CaptureRequest/session-output substitution with drained PRIVATE ImageReader outputs
- `SharedEglContextRegistry`: WebRTC EGL context handoff to optional outputs
- `SurfaceFrameOutput`: returned-frame renderer for transferred Zoom target surfaces
- `RootLabBrokerDiagnostics`: process-local device-validation snapshot exposed to the rootlab APK UI

## Camera redirection behavior

The current Zoom path is no longer the earlier surface-observation-only implementation.

A new redirect is selected only when the broker reports both:

- shared WebRTC EGL context available
- returned WebRTC stream available

When redirect readiness is satisfied:

1. Zoom `CaptureRequest.Builder.addTarget/removeTarget` surfaces are mapped to dummy PRIVATE `ImageReader` surfaces.
2. The matching Camera2 capture-session outputs are replaced with those same dummy surfaces.
3. The physical camera producer continues to feed the drained dummy outputs.
4. Zoom's original output surfaces are transferred to OmniLive through the rootlab-only Messenger broker.
5. OmniLive renders returned WebRTC frames to those original surfaces using the shared EGL context.

The implementation supports common Android Camera2 shapes:

- legacy `List<Surface>`
- `OutputConfiguration`
- `SessionConfiguration`
- `prepare`
- `tearDown`
- `updateOutputConfiguration`
- `finalizeOutputConfigurations`

Request and session decisions are sticky. If request targets were created before redirect readiness, that Camera2 session remains physical. Once a session commits to redirection, a temporary returned-stream gap does not mix physical and redirected targets.

Unsupported output shapes or a not-ready broker remain fail-open on the physical camera before a redirect is committed.

## Phone-visible rootlab diagnostics

Rootlab APKs now expose live validation state directly in the OmniLive diagnostics screen.

The Zoom process publishes its hook/camera counters to the OmniLive broker, including:

- manager/session/close hook counts
- camera-open request count
- capture-session request count
- close-event count
- latest camera id
- latest target surface count
- latest hook error

The OmniLive broker tracks:

- Zoom broker connection
- broker readiness
- EGL readiness
- returned-stream readiness
- requested target surfaces
- successfully attached target surfaces
- successful returned-frame presentations

The normal app reads this state only through reflection when `BuildConfig.ROOT_LAB_ENABLED` is true, so normal APKs have no compile-time dependency on rootlab.

A useful successful device state looks like:

```text
Zoom connected · broker ready · EGL yes · stream yes · surfaces 1/1 · presented 1 · hooks 1/...
```

The exact hook counts vary by Android/Zoom version.

## Recovery behavior

Default policy:

- up to 3 rebind attempts
- exponential retry delay starting at 350 ms, capped at 10 s
- after exhaustion, restore the physical camera
- if fallback is unavailable/disabled, enter `ERROR`
- moving the target to background cancels stale recovery work
- closing the runtime restores physical camera state and stops the session

A processed-output rebind alone does not prove the target camera reopened; a target-camera lifecycle event is still required before the compatibility state becomes `ACTIVE`.

## Rooted-device smoke gate

Use the rootlab APK, not the normal APK.

```bash
OMNILIVE_ROOTLAB_MODE=1 \
OMNILIVE_ROOTLAB_LAUNCH_TARGET=1 \
OMNILIVE_ROOTLAB_REQUIRE_HOOKS=1 \
OMNILIVE_ROOTLAB_REQUIRE_REDIRECT=1 \
OMNILIVE_ROOTLAB_REQUIRE_FRAME_PRESENTED=1 \
bash ops/android-device-smoke.sh build-artifacts/omnilive-rootlab-debug.apk
```

The smoke harness verifies:

- root shell availability
- rootlab broker packaged
- Zoom package installed
- Xposed Camera2 hook inventory observed
- Camera2 redirection observed
- broker readiness
- target-surface attachment
- actual returned-frame presentation
- no foreground-service/camera security crash

The phone UI and “Copy diagnostics” action provide an additional validation channel when ADB/logcat access is inconvenient.

## Remaining device-specific gate

The remaining rootlab uncertainty is no longer source compilation or surface substitution. It is empirical validation on the actual rooted/OEM device that Zoom displays the returned OmniLive frames correctly and remains stable through camera lifecycle transitions.

Do not claim this final gate is complete until a rooted-device run confirms:

- Xposed hooks activate in the installed Zoom version
- a redirect session is selected
- at least one returned frame is presented to a Zoom target surface
- Zoom visibly displays the returned frame stream
- stopping/restarting the camera restores a valid path
