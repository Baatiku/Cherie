package com.baatiku.omnilive.stream

class AdaptiveQualityController(initial:String="balanced") {
    var profileName = initial
        private set
    private var healthySamples = 0
    fun update(rttMs:Long, packetLoss:Double, backendP95Ms:Long):String {
        if (rttMs > 180 || packetLoss > 0.06 || backendP95Ms > 55) {
            healthySamples = 0
            profileName = if (profileName == "high") "balanced" else "low_latency"
            return profileName
        }
        if (rttMs < 110 && packetLoss < 0.02 && backendP95Ms < 35) {
            healthySamples++
            if (healthySamples >= 8) {
                profileName = when(profileName) { "low_latency" -> "balanced"; "balanced" -> "high"; else -> "high" }
                healthySamples = 0
            }
        } else healthySamples = 0
        return profileName
    }
}
