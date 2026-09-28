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
    val frameLatencyP50Ms:Long?=null,
    val frameLatencyP95Ms:Long?=null,
    val frameLatencySamples:Long=0,
    val frameCorrelationMisses:Long=0,
    val frameCorrelationOutOfOrder:Long=0,
    val backendFrameTotalMs:Long?=null,
    val iceLocalCandidateType:String?=null,
    val iceRemoteCandidateType:String?=null,
    val iceTransport:String?=null,
    val relayUsed:Boolean?=null,
    val bitrateTier:String?=null,
    val targetBitrateKbps:Int?=null,
    val sentKbps:Long?=null,
    val receivedKbps:Long?=null,
    val availableOutgoingKbps:Long?=null,
    val encoderFps:Double?=null,
    val framesEncoded:Long?=null,
    val framesSent:Long?=null,
    val lastError:String?=null
)

object LiveTelemetry {
    private val mutable=MutableStateFlow(LiveTelemetryState())
    val state:StateFlow<LiveTelemetryState> = mutable.asStateFlow()
    fun update(transform:(LiveTelemetryState)->LiveTelemetryState) { mutable.value=transform(mutable.value) }
    fun reset() { mutable.value=LiveTelemetryState() }
}
