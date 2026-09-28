package com.baatiku.omnilive.stream

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class LiveTelemetryState(
    val connectionState:String="idle",
    val iceState:String="new",
    val networkState:String="unknown",
    val activeProfile:String="balanced",
    val activeEffect:String="original",
    val rttMs:Long?=null,
    val packetLoss:Double?=null,
    val packetsLost:Long?=null,
    val framesReceived:Long?=null,
    val backendProcessorP95Ms:Long?=null,
    val backendDetectorP95Ms:Long?=null,
    val backendModelP95Ms:Long?=null,
    val targetNeuralFps:Double?=null,
    val droppedInferenceFrames:Long?=null,
    val inputOverwrittenFrames:Long?=null,
    val lastError:String?=null
)

object LiveTelemetry {
    private val mutable=MutableStateFlow(LiveTelemetryState())
    val state:StateFlow<LiveTelemetryState> = mutable.asStateFlow()
    fun update(transform:(LiveTelemetryState)->LiveTelemetryState) { mutable.value=transform(mutable.value) }
    fun reset() { mutable.value=LiveTelemetryState() }
}
