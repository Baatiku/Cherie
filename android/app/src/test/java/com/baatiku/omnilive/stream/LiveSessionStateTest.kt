package com.baatiku.omnilive.stream

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveSessionStateTest {
    @After
    fun reset() {
        LiveSessionState.idle()
    }

    @Test
    fun startActiveStopIdleLifecycleIsObservable() {
        LiveSessionState.starting("balanced","original","https://example.test")
        assertEquals(LiveSessionPhase.STARTING,LiveSessionState.state.value.phase)
        assertTrue(LiveSessionState.state.value.isRunning)

        LiveSessionState.active()
        assertEquals(LiveSessionPhase.ACTIVE,LiveSessionState.state.value.phase)
        assertTrue(LiveSessionState.state.value.isRunning)

        LiveSessionState.stopping()
        assertEquals(LiveSessionPhase.STOPPING,LiveSessionState.state.value.phase)
        assertTrue(LiveSessionState.state.value.isRunning)

        LiveSessionState.idle()
        assertEquals(LiveSessionPhase.IDLE,LiveSessionState.state.value.phase)
        assertFalse(LiveSessionState.state.value.isRunning)
    }

    @Test
    fun failuresRemainVisibleWithoutPretendingServiceIsRunning() {
        LiveSessionState.starting("high","custom_onnx","https://example.test")
        LiveSessionState.failed(IllegalStateException("camera unavailable"))

        val snapshot=LiveSessionState.state.value
        assertEquals(LiveSessionPhase.ERROR,snapshot.phase)
        assertEquals("camera unavailable",snapshot.error)
        assertFalse(snapshot.isRunning)
        assertEquals("high",snapshot.profile)
        assertEquals("custom_onnx",snapshot.effect)
    }
}
