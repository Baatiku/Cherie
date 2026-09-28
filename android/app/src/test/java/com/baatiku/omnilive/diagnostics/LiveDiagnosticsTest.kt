package com.baatiku.omnilive.diagnostics

import com.baatiku.omnilive.stream.LiveTelemetryState
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveDiagnosticsTest {
    @Test
    fun reportIncludesConnectionTransportReferenceAndErrors() {
        val text=LiveDiagnostics.asText(
            LiveTelemetryState(
                connectionState="connected",
                iceState="connected",
                networkState="wifi",
                activeProfile="balanced",
                activeEffect="original",
                rttMs=42,
                packetLoss=0.0125,
                iceLocalCandidateType="srflx",
                iceRemoteCandidateType="host",
                iceTransport="udp",
                relayUsed=false,
                frameLatencyP50Ms=85,
                frameLatencyP95Ms=120,
                frameLatencySamples=12,
                thermalStatus="severe",
                thermalQualityCap="low_latency",
                referenceImageStatus="ready",
                referenceImageWidth=768,
                referenceImageHeight=768,
                lastError="example"
            )
        )

        assertTrue(text.contains("Connection: connected"))
        assertTrue(text.contains("RTT/loss: 42ms / 1.25%"))
        assertTrue(text.contains("srflx -> host / udp / direct"))
        assertTrue(text.contains("Frame RT p50/p95: 85/120 ms n=12"))
        assertTrue(text.contains("Thermal: severe · cap low_latency"))
        assertTrue(text.contains("Reference: ready 768x768"))
        assertTrue(text.contains("Last error: example"))
    }
}
