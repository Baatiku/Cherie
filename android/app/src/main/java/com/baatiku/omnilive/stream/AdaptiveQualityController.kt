package com.baatiku.omnilive.stream

class AdaptiveQualityController(initial:String="balanced") {
    var profileName=normalize(initial)
        private set
    var thermalCap:String="high"
        private set

    private var healthySamples=0
    private var thermalRecoverySamples=RECOVERY_HOLD_SAMPLES

    fun update(
        rttMs:Long,
        packetLoss:Double,
        backendP95Ms:Long,
        thermalStatus:String="unknown"
    ):String {
        thermalCap=capForThermalStatus(thermalStatus)
        updateThermalRecovery(thermalStatus)

        val degraded=
            rttMs>180 ||
            packetLoss>0.06 ||
            backendP95Ms>55

        if(degraded) {
            healthySamples=0
            profileName=stepDown(profileName)
        } else {
            val healthy=
                rttMs<110 &&
                packetLoss<0.02 &&
                backendP95Ms<35

            if(healthy && thermalRecoverySamples>=RECOVERY_HOLD_SAMPLES) {
                healthySamples++
                if(healthySamples>=NETWORK_UPGRADE_SAMPLES) {
                    profileName=stepUp(profileName)
                    healthySamples=0
                }
            } else {
                healthySamples=0
            }
        }

        profileName=applyCap(profileName,thermalCap)
        return profileName
    }

    fun applyThermalStatus(thermalStatus:String):String {
        thermalCap=capForThermalStatus(thermalStatus)
        updateThermalRecovery(thermalStatus)
        profileName=applyCap(profileName,thermalCap)
        return profileName
    }

    private fun updateThermalRecovery(thermalStatus:String) {
        when(normalizeThermal(thermalStatus)) {
            "moderate","severe","critical","emergency","shutdown" -> {
                thermalRecoverySamples=0
                healthySamples=0
            }
            "none","light" -> {
                thermalRecoverySamples=(thermalRecoverySamples+1).coerceAtMost(RECOVERY_HOLD_SAMPLES)
            }
        }
    }

    companion object {
        private const val NETWORK_UPGRADE_SAMPLES=8
        private const val RECOVERY_HOLD_SAMPLES=12

        fun capForThermalStatus(status:String):String = when(normalizeThermal(status)) {
            "moderate" -> "balanced"
            "severe","critical","emergency","shutdown" -> "low_latency"
            else -> "high"
        }

        private fun normalizeThermal(status:String)=status.trim().lowercase()

        private fun normalize(name:String)=when(name) {
            "high","balanced","low_latency" -> name
            else -> "balanced"
        }

        private fun rank(name:String)=when(name) {
            "low_latency" -> 0
            "balanced" -> 1
            "high" -> 2
            else -> 1
        }

        private fun applyCap(profile:String,cap:String):String =
            if(rank(profile)<=rank(cap)) profile else cap

        private fun stepDown(profile:String)=when(profile) {
            "high" -> "balanced"
            else -> "low_latency"
        }

        private fun stepUp(profile:String)=when(profile) {
            "low_latency" -> "balanced"
            "balanced" -> "high"
            else -> "high"
        }
    }
}
