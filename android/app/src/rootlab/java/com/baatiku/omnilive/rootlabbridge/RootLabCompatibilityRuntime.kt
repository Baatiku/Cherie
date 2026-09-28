package com.baatiku.omnilive.rootlabbridge

import android.os.Handler
import android.os.Looper
import com.baatiku.omnilive.rootlab.CameraCompatibilityAdapter
import com.baatiku.omnilive.rootlab.CameraRecoveryPolicy
import com.baatiku.omnilive.rootlab.CompatibilityRecoveryCoordinator
import com.baatiku.omnilive.rootlab.CompatibilitySessionController
import com.baatiku.omnilive.rootlab.LabFrameEndpoint
import com.baatiku.omnilive.rootlab.LabSessionObserver
import com.baatiku.omnilive.rootlab.LabSessionState
import com.baatiku.omnilive.rootlab.LabTarget
import com.baatiku.omnilive.rootlab.RecoveryScheduler
import com.baatiku.omnilive.rootlab.RootCapability
import com.baatiku.omnilive.rootlab.RootEnvironmentProbe
import java.io.Closeable
import java.util.concurrent.TimeUnit

object RootLabTargets {
    const val ZOOM_PACKAGE="us.zoom.videomeetings"

    fun zoom():LabTarget=LabTarget(ZOOM_PACKAGE)
    fun custom(packageName:String):LabTarget=LabTarget(packageName)
}

class AndroidRecoveryScheduler(
    private val handler:Handler=Handler(Looper.getMainLooper())
):RecoveryScheduler {
    override fun schedule(delayMs:Long,block:()->Unit) {
        handler.postDelayed(block,delayMs.coerceAtLeast(0L))
    }
}

class SuRootEnvironmentProbe(
    private val timeoutMs:Long=2_000L
):RootEnvironmentProbe {
    override fun probe():RootCapability {
        return try {
            val process=ProcessBuilder("su","-c","id")
                .redirectErrorStream(true)
                .start()
            val completed=process.waitFor(timeoutMs,TimeUnit.MILLISECONDS)
            if(!completed) {
                process.destroyForcibly()
                RootCapability(false,"su probe timed out")
            } else {
                val output=process.inputStream.bufferedReader().use { it.readText() }.trim()
                if(process.exitValue()==0 && output.contains("uid=0")) {
                    RootCapability(true,output.ifBlank { "root shell available" })
                } else {
                    RootCapability(
                        false,
                        output.ifBlank { "su exited with "+process.exitValue() }
                    )
                }
            }
        } catch(t:Throwable) {
            RootCapability(false,t.message ?: t.javaClass.simpleName)
        }
    }
}

/**
 * Lab-only composition root for a package-scoped camera compatibility experiment.
 *
 * Pixel delivery stays on the existing ProcessedFrameOutput/SurfaceFrameOutput path. A concrete
 * CameraCompatibilityAdapter owns the rooted/device-specific camera redirection mechanism.
 * This runtime owns root probing, target lifecycle state, retry/fallback policy and processed-frame
 * availability so adapters don't duplicate those concerns.
 */
class RootLabCompatibilityRuntime(
    adapter:CameraCompatibilityAdapter,
    endpoint:LabFrameEndpoint,
    target:LabTarget=RootLabTargets.zoom(),
    rootProbe:RootEnvironmentProbe=SuRootEnvironmentProbe(),
    scheduler:RecoveryScheduler=AndroidRecoveryScheduler(),
    recoveryPolicy:CameraRecoveryPolicy=CameraRecoveryPolicy(),
    observer:LabSessionObserver?=null
):Closeable {
    val controller=CompatibilitySessionController(target,rootProbe,observer)
    private val recovery=CompatibilityRecoveryCoordinator(
        controller=controller,
        adapter=adapter,
        scheduler=scheduler,
        policy=recoveryPolicy,
        observer=observer
    )
    private val frameBridge=RootLabProcessedFrameBridge(controller,endpoint)

    fun start():LabSessionState {
        frameBridge.start()
        val state=recovery.start()
        if(state==LabSessionState.ERROR) frameBridge.close()
        return state
    }

    fun onTargetForegroundChanged(foreground:Boolean):LabSessionState =
        recovery.onTargetForegroundChanged(foreground)

    fun onTargetCameraChanged(open:Boolean):LabSessionState =
        recovery.onTargetCameraChanged(open)

    fun onCompatibilityPathError(detail:String):LabSessionState =
        recovery.onCompatibilityPathError(detail)

    override fun close() {
        frameBridge.close()
        recovery.close()
    }
}
