package com.baatiku.omnilive.stream

data class QualityProfile(
    val width:Int,
    val height:Int,
    val cameraFps:Int,
    val inferenceFps:Int,
    val detectorInterval:Int,
    val maxBitrateBps:Int
) {
    companion object {
        fun fromName(name:String) = when(name) {
            "low_latency" -> QualityProfile(640,360,20,10,12,700_000)
            "high" -> QualityProfile(1280,720,30,18,6,2_500_000)
            else -> QualityProfile(960,540,24,14,8,1_400_000)
        }
    }
}
