package com.baatiku.omnilive.diagnostics

import com.baatiku.omnilive.stream.LiveTelemetryState
import java.util.Locale

object LiveDiagnostics {
    fun asText(state:LiveTelemetryState):String=buildString {
        appendLine("Live session")
        appendLine("Connection: ${state.connectionState}")
        appendLine("ICE: ${state.iceState}")
        appendLine("Network: ${state.networkState}")
        appendLine("Profile/effect: ${state.activeProfile} / ${state.activeEffect}")
        appendLine(
            "RTT/loss: ${state.rttMs?.let { "${it}ms" } ?: "—"} / "+
                (state.packetLoss?.let { String.format(Locale.US,"%.2f%%",it*100.0) } ?: "—")
        )
        appendLine(
            "ICE path: ${state.iceLocalCandidateType ?: "—"} -> "+
                "${state.iceRemoteCandidateType ?: "—"} / ${state.iceTransport ?: "—"} / "+
                when(state.relayUsed) { true -> "relay"; false -> "direct"; null -> "—" }
        )
        appendLine(
            "Frame RT p50/p95: ${state.frameLatencyP50Ms ?: "—"}/"+
                "${state.frameLatencyP95Ms ?: "—"} ms n=${state.frameLatencySamples}"
        )
        appendLine(
            "Frame correlation misses/out-of-order: ${state.frameCorrelationMisses}/"+
                "${state.frameCorrelationOutOfOrder}"
        )
        appendLine("Backend frame total: ${state.backendFrameTotalMs ?: "—"} ms")
        appendLine(
            "Backend p95 processor/detector/model: ${state.backendProcessorP95Ms ?: "—"}/"+
                "${state.backendDetectorP95Ms ?: "—"}/${state.backendModelP95Ms ?: "—"} ms"
        )
        appendLine(
            "Neural target/drop/overwrite: "+
                "${state.targetNeuralFps?.let { String.format(Locale.US,"%.2f",it) } ?: "—"} / "+
                "${state.droppedInferenceFrames ?: "—"} / ${state.inputOverwrittenFrames ?: "—"}"
        )
        appendLine(
            "Video kbps tx/rx/available: ${state.sentKbps ?: "—"}/"+
                "${state.receivedKbps ?: "—"}/${state.availableOutgoingKbps ?: "—"}"
        )
        appendLine(
            "Encoder fps/frames: "+
                "${state.encoderFps?.let { String.format(Locale.US,"%.2f",it) } ?: "—"} / "+
                "${state.framesEncoded ?: "—"} encoded / ${state.framesSent ?: "—"} sent"
        )
        appendLine(
            "Bitrate tier/target: ${state.bitrateTier ?: "—"} / "+
                "${state.targetBitrateKbps?.let { "${it}kbps" } ?: "—"}"
        )
        appendLine(
            "Battery: ${state.batteryPercent?.let { "${it}%" } ?: "—"} / "+
                when(state.batteryCharging) { true -> "charging"; false -> "not charging"; null -> "—" }+
                " / ${state.batteryTemperatureC?.let { String.format(Locale.US,"%.1fC",it) } ?: "—"}"
        )
        appendLine("Thermal: ${state.thermalStatus} · cap ${state.thermalQualityCap}")
        appendLine(
            "Reference: ${state.referenceImageStatus} "+
                if(state.referenceImageWidth!=null && state.referenceImageHeight!=null)
                    "${state.referenceImageWidth}x${state.referenceImageHeight}" else ""
        )
        state.referenceImageError?.let { appendLine("Reference error: $it") }
        state.lastError?.let { appendLine("Last error: $it") }
    }.trim()
}
