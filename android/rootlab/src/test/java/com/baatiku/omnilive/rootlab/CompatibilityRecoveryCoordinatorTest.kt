package com.baatiku.omnilive.rootlab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.ArrayDeque

class CompatibilityRecoveryCoordinatorTest {
    private class ManualScheduler:RecoveryScheduler {
        private val queue=ArrayDeque<()->Unit>()
        override fun schedule(delayMs:Long,block:()->Unit) {
            queue.addLast(block)
        }
        fun runNext() {
            queue.removeFirst().invoke()
        }
        fun hasPending():Boolean=queue.isNotEmpty()
    }

    private class FakeAdapter(
        var attachResult:Boolean=true,
        bindResults:List<Boolean> = listOf(true),
        var fallbackResult:Boolean=true
    ):CameraCompatibilityAdapter {
        private val results=ArrayDeque(bindResults)
        var attachCount=0
        var bindCount=0
        var fallbackCount=0
        var closed=false

        override fun attach(target:LabTarget):Boolean {
            attachCount++
            return attachResult
        }

        override fun bindProcessedOutput():Boolean {
            bindCount++
            return if(results.isEmpty()) true else results.removeFirst()
        }

        override fun restorePhysicalCamera():Boolean {
            fallbackCount++
            return fallbackResult
        }

        override fun close() {
            closed=true
        }
    }

    private fun controller()=CompatibilitySessionController(
        LabTarget("us.zoom.videomeetings"),
        RootEnvironmentProbe { RootCapability(true) }
    )

    @Test
    fun successful_bind_still_waits_for_real_target_camera_open() {
        val scheduler=ManualScheduler()
        val adapter=FakeAdapter(bindResults=listOf(true))
        val controller=controller()
        val coordinator=CompatibilityRecoveryCoordinator(controller,adapter,scheduler)

        assertEquals(LabSessionState.PAUSED,coordinator.start())
        assertEquals(LabSessionState.WAITING_FOR_OUTPUT,coordinator.onTargetForegroundChanged(true))
        assertEquals(LabSessionState.WAITING_FOR_CAMERA,coordinator.onOutputAvailabilityChanged(true))
        assertEquals(1,adapter.bindCount)
        assertEquals(LabSessionState.ACTIVE,coordinator.onTargetCameraChanged(true))
    }

    @Test
    fun failed_bind_retries_without_faking_camera_lifecycle() {
        val scheduler=ManualScheduler()
        val adapter=FakeAdapter(bindResults=listOf(false,true))
        val controller=controller()
        val coordinator=CompatibilityRecoveryCoordinator(controller,adapter,scheduler)

        coordinator.start()
        coordinator.onTargetForegroundChanged(true)
        assertEquals(LabSessionState.RECOVERING,coordinator.onOutputAvailabilityChanged(true))
        assertTrue(scheduler.hasPending())

        scheduler.runNext()

        assertEquals(2,adapter.bindCount)
        assertEquals(LabSessionState.WAITING_FOR_CAMERA,controller.state)
        assertFalse(controller.targetCameraOpen)
    }

    @Test
    fun exhausted_rebind_attempts_restore_physical_camera() {
        val scheduler=ManualScheduler()
        val adapter=FakeAdapter(bindResults=listOf(false,false),fallbackResult=true)
        val controller=controller()
        val coordinator=CompatibilityRecoveryCoordinator(
            controller,
            adapter,
            scheduler,
            CameraRecoveryPolicy(maxRebindAttempts=1,retryBaseDelayMs=1)
        )

        coordinator.start()
        coordinator.onTargetForegroundChanged(true)
        coordinator.onOutputAvailabilityChanged(true)
        scheduler.runNext()

        assertEquals(1,adapter.fallbackCount)
        assertEquals(LabSessionState.RECOVERING,controller.state)
    }

    @Test
    fun exhausted_recovery_without_fallback_enters_error() {
        val scheduler=ManualScheduler()
        val adapter=FakeAdapter(bindResults=listOf(false),fallbackResult=false)
        val controller=controller()
        val coordinator=CompatibilityRecoveryCoordinator(
            controller,
            adapter,
            scheduler,
            CameraRecoveryPolicy(maxRebindAttempts=0,retryBaseDelayMs=1,allowPhysicalFallback=false)
        )

        coordinator.start()
        coordinator.onTargetForegroundChanged(true)
        val state=coordinator.onOutputAvailabilityChanged(true)

        assertEquals(LabSessionState.ERROR,state)
    }

    @Test
    fun close_restores_physical_camera_and_stops_session() {
        val scheduler=ManualScheduler()
        val adapter=FakeAdapter()
        val controller=controller()
        val coordinator=CompatibilityRecoveryCoordinator(controller,adapter,scheduler)

        coordinator.start()
        coordinator.close()

        assertEquals(1,adapter.fallbackCount)
        assertTrue(adapter.closed)
        assertEquals(LabSessionState.STOPPED,controller.state)
    }
}
