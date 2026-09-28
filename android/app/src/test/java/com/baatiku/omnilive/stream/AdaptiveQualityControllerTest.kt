package com.baatiku.omnilive.stream

import org.junit.Assert.assertEquals
import org.junit.Test

class AdaptiveQualityControllerTest {
    @Test
    fun severeThermalStatusForcesLowLatencyImmediately() {
        val controller=AdaptiveQualityController("high")

        val next=controller.update(
            rttMs=60,
            packetLoss=0.0,
            backendP95Ms=15,
            thermalStatus="severe"
        )

        assertEquals("low_latency",next)
        assertEquals("low_latency",controller.thermalCap)
    }

    @Test
    fun moderateThermalStatusCapsHighAtBalanced() {
        val controller=AdaptiveQualityController("high")

        val next=controller.applyThermalStatus("moderate")

        assertEquals("balanced",next)
        assertEquals("balanced",controller.thermalCap)
    }

    @Test
    fun qualityDoesNotJumpUpImmediatelyAfterHeatClears() {
        val controller=AdaptiveQualityController("high")
        controller.applyThermalStatus("critical")
        assertEquals("low_latency",controller.profileName)

        repeat(18) {
            controller.update(
                rttMs=60,
                packetLoss=0.0,
                backendP95Ms=15,
                thermalStatus="none"
            )
        }
        assertEquals("low_latency",controller.profileName)

        controller.update(
            rttMs=60,
            packetLoss=0.0,
            backendP95Ms=15,
            thermalStatus="none"
        )
        assertEquals("balanced",controller.profileName)
    }

    @Test
    fun normalNetworkAdaptationStillUpgradesAfterHealthySamples() {
        val controller=AdaptiveQualityController("balanced")

        repeat(8) {
            controller.update(
                rttMs=60,
                packetLoss=0.0,
                backendP95Ms=15,
                thermalStatus="light"
            )
        }

        assertEquals("high",controller.profileName)
        assertEquals("high",controller.thermalCap)
    }

    @Test
    fun degradedNetworkStillStepsQualityDown() {
        val controller=AdaptiveQualityController("high")

        val next=controller.update(
            rttMs=220,
            packetLoss=0.08,
            backendP95Ms=70,
            thermalStatus="none"
        )

        assertEquals("balanced",next)
    }
}
