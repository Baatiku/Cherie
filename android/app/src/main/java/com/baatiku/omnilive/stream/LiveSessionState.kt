package com.baatiku.omnilive.stream

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class LiveSessionPhase {
    IDLE,
    STARTING,
    ACTIVE,
    STOPPING,
    ERROR
}

data class LiveSessionSnapshot(
    val phase:LiveSessionPhase=LiveSessionPhase.IDLE,
    val profile:String?=null,
    val effect:String?=null,
    val backendUrl:String?=null,
    val error:String?=null
) {
    val isRunning:Boolean
        get()=phase==LiveSessionPhase.STARTING ||
            phase==LiveSessionPhase.ACTIVE ||
            phase==LiveSessionPhase.STOPPING
}

object LiveSessionState {
    private val mutable=MutableStateFlow(LiveSessionSnapshot())
    val state:StateFlow<LiveSessionSnapshot> = mutable.asStateFlow()

    fun starting(profile:String,effect:String,backendUrl:String) {
        mutable.value=LiveSessionSnapshot(
            phase=LiveSessionPhase.STARTING,
            profile=profile,
            effect=effect,
            backendUrl=backendUrl
        )
    }

    fun active() {
        mutable.value=mutable.value.copy(phase=LiveSessionPhase.ACTIVE,error=null)
    }

    fun stopping() {
        mutable.value=mutable.value.copy(phase=LiveSessionPhase.STOPPING)
    }

    fun idle() {
        mutable.value=LiveSessionSnapshot()
    }

    fun failed(error:Throwable) {
        mutable.value=mutable.value.copy(
            phase=LiveSessionPhase.ERROR,
            error=error.message ?: error.javaClass.simpleName
        )
    }

    fun failed(message:String) {
        mutable.value=mutable.value.copy(phase=LiveSessionPhase.ERROR,error=message)
    }
}
