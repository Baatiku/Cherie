package com.baatiku.omnilive.rootlab

class CompatibilitySessionController(
    private val target:LabTarget,
    private val rootProbe:RootEnvironmentProbe,
    private val observer:LabSessionObserver?=null
) {
    @Volatile var state:LabSessionState=LabSessionState.IDLE
        private set

    @Volatile var targetForeground:Boolean=false
        private set

    @Volatile var outputAvailable:Boolean=false
        private set

    @Volatile var targetCameraOpen:Boolean=false
        private set

    @Volatile var rootCapability:RootCapability?=null
        private set

    @Synchronized
    fun start():LabSessionState {
        if(state!=LabSessionState.IDLE && state!=LabSessionState.STOPPED) return state
        transition(LabSessionState.ATTACHING,"starting compatibility session for " + target.packageName)
        val capability=rootProbe.probe()
        rootCapability=capability
        if(!capability.available) {
            transition(LabSessionState.ERROR,capability.detail ?: "root capability unavailable")
            return state
        }
        reconcile("root capability ready")
        return state
    }

    @Synchronized
    fun stop():LabSessionState {
        targetForeground=false
        targetCameraOpen=false
        outputAvailable=false
        transition(LabSessionState.STOPPED,"compatibility session stopped")
        return state
    }

    @Synchronized
    fun onTargetForegroundChanged(foreground:Boolean):LabSessionState {
        targetForeground=foreground
        if(!foreground && state==LabSessionState.ACTIVE) {
            transition(LabSessionState.PAUSED,"target moved to background")
            return state
        }
        reconcile(if(foreground) "target foreground" else "target background")
        return state
    }

    @Synchronized
    fun onOutputAvailabilityChanged(available:Boolean):LabSessionState {
        outputAvailable=available
        if(!available && state==LabSessionState.ACTIVE) {
            transition(LabSessionState.RECOVERING,"processed output unavailable")
            return state
        }
        reconcile(if(available) "processed output available" else "processed output unavailable")
        return state
    }

    @Synchronized
    fun onTargetCameraChanged(open:Boolean):LabSessionState {
        targetCameraOpen=open
        if(!open && state==LabSessionState.ACTIVE) {
            transition(LabSessionState.RECOVERING,"target released camera")
            return state
        }
        reconcile(if(open) "target camera open" else "target camera closed")
        return state
    }

    @Synchronized
    fun onPhysicalCameraFallback():LabSessionState {
        if(state==LabSessionState.STOPPED || state==LabSessionState.ERROR) return state
        transition(LabSessionState.RECOVERING,"physical camera fallback active")
        return state
    }

    @Synchronized
    fun onRecoverableError(detail:String):LabSessionState {
        if(state==LabSessionState.STOPPED) return state
        transition(LabSessionState.RECOVERING,detail)
        return state
    }

    @Synchronized
    fun onFatalError(detail:String):LabSessionState {
        if(state==LabSessionState.STOPPED) return state
        transition(LabSessionState.ERROR,detail)
        return state
    }

    private fun reconcile(reason:String) {
        if(state==LabSessionState.STOPPED || state==LabSessionState.ERROR) return
        if(rootCapability?.available!=true) {
            transition(LabSessionState.ATTACHING,reason)
            return
        }
        if(!targetForeground) {
            transition(LabSessionState.PAUSED,"waiting for target foreground")
            return
        }
        if(!outputAvailable) {
            transition(LabSessionState.WAITING_FOR_OUTPUT,"waiting for OmniLive processed output")
            return
        }
        if(!targetCameraOpen) {
            transition(LabSessionState.WAITING_FOR_CAMERA,"waiting for target camera lifecycle")
            return
        }
        transition(LabSessionState.ACTIVE,"compatibility path ready")
    }

    private fun transition(next:LabSessionState,reason:String) {
        if(state==next) return
        state=next
        observer?.onStateChanged(next,reason)
    }
}
