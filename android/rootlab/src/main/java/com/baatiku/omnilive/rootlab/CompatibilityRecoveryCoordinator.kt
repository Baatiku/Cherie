package com.baatiku.omnilive.rootlab

import java.io.Closeable

data class CameraRecoveryPolicy(
    val maxRebindAttempts:Int=3,
    val retryBaseDelayMs:Long=350L,
    val allowPhysicalFallback:Boolean=true
) {
    init {
        require(maxRebindAttempts>=0)
        require(retryBaseDelayMs>=0)
    }

    fun delayForAttempt(attempt:Int):Long {
        if(attempt<=1) return retryBaseDelayMs
        val shift=(attempt-1).coerceAtMost(5)
        return (retryBaseDelayMs*(1L shl shift)).coerceAtMost(10_000L)
    }
}

interface CameraCompatibilityAdapter:Closeable {
    fun attach(target:LabTarget):Boolean
    fun bindProcessedOutput():Boolean
    fun restorePhysicalCamera():Boolean
    override fun close() {}
}

fun interface RecoveryScheduler {
    fun schedule(delayMs:Long,block:()->Unit)
}

class CompatibilityRecoveryCoordinator(
    private val controller:CompatibilitySessionController,
    private val adapter:CameraCompatibilityAdapter,
    private val scheduler:RecoveryScheduler,
    private val policy:CameraRecoveryPolicy=CameraRecoveryPolicy(),
    private val observer:LabSessionObserver?=null
):Closeable {
    private var closed=false
    private var recoveryGeneration=0L
    private var rebindAttempts=0

    @Synchronized
    fun start():LabSessionState {
        if(closed) return LabSessionState.STOPPED
        val state=controller.start()
        if(state==LabSessionState.ERROR) return state
        if(!adapter.attach(controller.target)) {
            return recover("target compatibility attach failed")
        }
        return state
    }

    @Synchronized
    fun onTargetForegroundChanged(foreground:Boolean):LabSessionState {
        if(closed) return LabSessionState.STOPPED
        val state=controller.onTargetForegroundChanged(foreground)
        if(!foreground) cancelPendingRecovery()
        return state
    }

    @Synchronized
    fun onOutputAvailabilityChanged(available:Boolean):LabSessionState {
        if(closed) return LabSessionState.STOPPED
        controller.onOutputAvailabilityChanged(available)
        if(available && controller.targetForeground) {
            ensureProcessedOutputBound()
        }
        return controller.state
    }

    @Synchronized
    fun onTargetCameraChanged(open:Boolean):LabSessionState {
        if(closed) return LabSessionState.STOPPED
        val state=controller.onTargetCameraChanged(open)
        if(open) {
            rebindAttempts=0
            cancelPendingRecovery()
            if(controller.outputAvailable) ensureProcessedOutputBound()
        } else if(controller.targetForeground && controller.outputAvailable) {
            recover("target camera lifecycle released")
        }
        return if(state==LabSessionState.ERROR) state else controller.state
    }

    @Synchronized
    fun onCompatibilityPathError(detail:String):LabSessionState {
        if(closed) return LabSessionState.STOPPED
        return recover(detail)
    }

    @Synchronized
    private fun ensureProcessedOutputBound():LabSessionState {
        if(closed || !controller.targetForeground || !controller.outputAvailable) return controller.state
        if(adapter.bindProcessedOutput()) {
            rebindAttempts=0
            return controller.onOutputAvailabilityChanged(true)
        }
        return recover("processed camera output bind failed")
    }

    @Synchronized
    private fun recover(detail:String):LabSessionState {
        controller.onRecoverableError(detail)
        if(closed) return controller.state

        if(rebindAttempts>=policy.maxRebindAttempts) {
            return fallbackOrFail(detail)
        }

        rebindAttempts++
        val attempt=rebindAttempts
        val generation=++recoveryGeneration
        val delay=policy.delayForAttempt(attempt)
        observer?.onStateChanged(
            LabSessionState.RECOVERING,
            detail+"; retry "+attempt+"/"+policy.maxRebindAttempts+" in "+delay+"ms"
        )
        scheduler.schedule(delay) {
            synchronized(this) {
                if(closed || generation!=recoveryGeneration) return@schedule
                if(!controller.targetForeground || !controller.outputAvailable) {
                    return@schedule
                }
                if(adapter.bindProcessedOutput()) {
                    rebindAttempts=0
                    controller.onOutputAvailabilityChanged(true)
                } else {
                    recover("processed camera output rebind failed")
                }
            }
        }
        return controller.state
    }

    @Synchronized
    private fun fallbackOrFail(detail:String):LabSessionState {
        cancelPendingRecovery()
        if(policy.allowPhysicalFallback && adapter.restorePhysicalCamera()) {
            controller.onPhysicalCameraFallback()
            observer?.onStateChanged(
                LabSessionState.RECOVERING,
                detail+"; recovery exhausted, physical camera restored"
            )
            return controller.state
        }
        return controller.onFatalError(detail+"; camera recovery exhausted")
    }

    @Synchronized
    private fun cancelPendingRecovery() {
        recoveryGeneration++
    }

    @Synchronized
    override fun close() {
        if(closed) return
        closed=true
        cancelPendingRecovery()
        try { adapter.restorePhysicalCamera() } catch(_:Throwable) {}
        try { adapter.close() } catch(_:Throwable) {}
        controller.stop()
    }
}
