package com.baatiku.omnilive.stream

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class LiveTelemetryState(
    val connectionState:String="idle",
    val iceState:String="new",
    val activeProfile:String="balanced",
    val rttMs:Long?=null,
    val packetLoss:Double?=null,
    val packetsLost:Long?=null,
    val framesReceived:Long?=null,
    val lastError:String?=null
)

object LiveTelemetry {
    private val mutable=MutableStateFlow(LiveTelemetryState())
    val state:StateFlow<LiveTelemetryState> = mutable.asStateFlow()
    fun update(transform:(LiveTelemetryState)->LiveTelemetryState) { mutable.value=transform(mutable.value) }
    fun reset() { mutable.value=LiveTelemetryState() }
}
