package com.baatiku.omnilive.rootlab

import org.junit.Assert.assertEquals
import org.junit.Test

class CompatibilitySessionControllerTest {
    private val rootAvailable=RootEnvironmentProbe { RootCapability(true) }

    @Test
    fun session_becomes_active_only_when_target_camera_and_output_are_ready() {
        val controller=CompatibilitySessionController(
            LabTarget("com.example.camera"),
            rootAvailable
        )

        assertEquals(LabSessionState.PAUSED,controller.start())
        assertEquals(LabSessionState.WAITING_FOR_OUTPUT,controller.onTargetForegroundChanged(true))
        assertEquals(LabSessionState.WAITING_FOR_CAMERA,controller.onOutputAvailabilityChanged(true))
        assertEquals(LabSessionState.ACTIVE,controller.onTargetCameraChanged(true))
    }

    @Test
    fun camera_release_moves_active_session_into_recovery_then_waiting() {
        val controller=CompatibilitySessionController(
            LabTarget("com.example.camera"),
            rootAvailable
        )
        controller.start()
        controller.onTargetForegroundChanged(true)
        controller.onOutputAvailabilityChanged(true)
        controller.onTargetCameraChanged(true)

        assertEquals(LabSessionState.RECOVERING,controller.onTargetCameraChanged(false))
        assertEquals(LabSessionState.ACTIVE,controller.onTargetCameraChanged(true))
    }

    @Test
    fun missing_root_capability_fails_closed_inside_lab_module() {
        val controller=CompatibilitySessionController(
            LabTarget("com.example.camera"),
            RootEnvironmentProbe { RootCapability(false,"root unavailable") }
        )
        assertEquals(LabSessionState.ERROR,controller.start())
    }
}
