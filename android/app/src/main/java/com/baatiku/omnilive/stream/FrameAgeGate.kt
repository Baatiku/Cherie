package com.baatiku.omnilive.stream
class FrameAgeGate(private val maxAgeMs:Long=140) {
    fun shouldProcess(capturedAtMs:Long, nowMs:Long=System.currentTimeMillis()) = nowMs - capturedAtMs <= maxAgeMs
}
