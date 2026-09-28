package com.baatiku.omnilive.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TransportPreflightTest {
    @Test
    fun websocketUrlFollowsBackendScheme() {
        assertEquals(
            "wss://example.com/v1/live",
            TransportPreflight.liveWebSocketUrl("https://example.com/")
        )
        assertEquals(
            "ws://10.0.2.2:8000/v1/live",
            TransportPreflight.liveWebSocketUrl("http://10.0.2.2:8000")
        )
    }

    @Test(expected=IllegalArgumentException::class)
    fun websocketUrlRejectsMissingScheme() {
        TransportPreflight.liveWebSocketUrl("example.com")
    }

    @Test
    fun summaryReportsRelayAvailability() {
        val result=TransportPreflightResult(
            health=BackendHealth(
                ok=true,
                status="ok",
                service="omnilive",
                modelConfigured=false,
                modelLoaded=false,
                providers=emptyList()
            ),
            iceServerCount=1,
            relayConfigured=false,
            sessionValidated=true,
            websocketOk=true,
            credentialMode="static"
        )
        assertTrue(result.summary().contains("Transport OK"))
        assertTrue(result.summary().contains("STUN/direct only"))
    }
}
