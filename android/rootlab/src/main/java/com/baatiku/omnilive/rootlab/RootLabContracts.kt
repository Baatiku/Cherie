package com.baatiku.omnilive.rootlab

enum class LabSessionState {
    IDLE,
    ATTACHING,
    WAITING_FOR_OUTPUT,
    WAITING_FOR_CAMERA,
    ACTIVE,
    PAUSED,
    RECOVERING,
    STOPPED,
    ERROR
}

data class LabTarget(
    val packageName:String
) {
    init {
        require(PACKAGE_PATTERN.matches(packageName)) { "Invalid Android package name" }
    }

    companion object {
        private val PACKAGE_PATTERN=Regex("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+")
    }
}

data class LabFrameInfo(
    val width:Int,
    val height:Int,
    val rotation:Int,
    val timestampNs:Long
)

interface LabFrameEndpoint {
    fun onFrameAvailable(info:LabFrameInfo)
    fun onOutputAvailabilityChanged(available:Boolean)
}

data class RootCapability(
    val available:Boolean,
    val detail:String?=null
)

fun interface RootEnvironmentProbe {
    fun probe():RootCapability
}

interface LabSessionObserver {
    fun onStateChanged(state:LabSessionState,reason:String)
}
