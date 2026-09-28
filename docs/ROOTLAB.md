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

## Remaining device-specific work

The package-specific/rooted camera redirection adapter is not implemented yet. That part depends on the actual rooted test device, Android/vendor camera stack and target app behavior. It should be implemented and validated inside this isolated contract instead of modifying normal OmniLive camera/WebRTC code.
