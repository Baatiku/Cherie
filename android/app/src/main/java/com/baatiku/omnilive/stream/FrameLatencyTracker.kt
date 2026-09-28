package com.baatiku.omnilive.stream

import kotlin.math.abs

data class FrameCaptureMark(
    val sequence:Long,
    val captureTimestampNs:Long,
    val captureElapsedNs:Long,
    val rtpGuess:Long
)

data class FrameTimingReport(
    val sequence:Long,
    val rtpTimestamp:Long,
    val backendTotalMs:Double,
    val backendProcessorMs:Double
)

data class FrameLatencySnapshot(
    val p50Ms:Long?,
    val p95Ms:Long?,
    val samples:Long,
    val misses:Long,
    val outOfOrder:Long,
    val backendTotalMs:Long?
)

class FrameLatencyTracker(
    private val maxPending:Int=96,
    private val maxSamples:Int=300
) {
    private data class Capture(val elapsedNs:Long)
    private data class PendingReport(val value:FrameTimingReport,val receivedAtNs:Long)
    private data class Returned(val rtpGuess:Long,val receivedAtNs:Long)

    private val lock=Any()
    private val captures=LinkedHashMap<Long,Capture>()
    private val reports=ArrayList<PendingReport>()
    private val returned=ArrayList<Returned>()
    private val latencyMs=ArrayList<Long>()
    private var nextSequence=0L
    private var returnOffset:Long?=null
    private var misses=0L
    private var outOfOrder=0L
    private var lastReportSequence=-1L
    private var latestBackendTotalMs:Long?=null

    fun newCapture(frameTimestampNs:Long,elapsedNs:Long):FrameCaptureMark = synchronized(lock) {
        nextSequence+=1
        val mark=FrameCaptureMark(
            sequence=nextSequence,
            captureTimestampNs=frameTimestampNs,
            captureElapsedNs=elapsedNs,
            rtpGuess=rtpTimestamp90k(frameTimestampNs)
        )
        captures[mark.sequence]=Capture(elapsedNs)
        trimLocked()
        mark
    }

    fun onTimingReport(report:FrameTimingReport,receivedAtNs:Long):FrameLatencySnapshot? = synchronized(lock) {
        if(report.sequence<=lastReportSequence) outOfOrder+=1
        if(report.sequence>lastReportSequence) lastReportSequence=report.sequence
        reports.add(PendingReport(report,receivedAtNs))
        latestBackendTotalMs=report.backendTotalMs.toLong()
        trimLocked()
        if(drainLocked()) snapshotLocked() else null
    }

    fun onReturnedFrame(frameTimestampNs:Long,receivedAtNs:Long):FrameLatencySnapshot? = synchronized(lock) {
        returned.add(Returned(rtpTimestamp90k(frameTimestampNs),receivedAtNs))
        trimLocked()
        if(drainLocked()) snapshotLocked() else null
    }

    fun snapshot():FrameLatencySnapshot = synchronized(lock) { snapshotLocked() }

    fun reset() = synchronized(lock) {
        captures.clear()
        reports.clear()
        returned.clear()
        latencyMs.clear()
        nextSequence=0
        returnOffset=null
        misses=0
        outOfOrder=0
        lastReportSequence=-1
        latestBackendTotalMs=null
    }

    private fun drainLocked():Boolean {
        if(reports.isEmpty() || returned.isEmpty()) return false
        if(returnOffset==null) calibrateReturnOffsetLocked()
        val offset=returnOffset ?: return false
        var matchedAny=false

        while(reports.isNotEmpty() && returned.isNotEmpty()) {
            var bestReport=-1
            var bestReturn=-1
            var bestCost=Int.MAX_VALUE
            val reportLimit=minOf(12,reports.size)
            val returnLimit=minOf(12,returned.size)
            for(ri in 0 until reportLimit) {
                val report=reports[ri].value
                for(fi in 0 until returnLimit) {
                    val corrected=normalizeRtp(returned[fi].rtpGuess+offset)
                    if(rtpDistance(corrected,report.rtpTimestamp)<=MATCH_TOLERANCE_TICKS) {
                        val cost=ri+fi
                        if(cost<bestCost) {
                            bestCost=cost
                            bestReport=ri
                            bestReturn=fi
                        }
                    }
                }
            }
            if(bestReport<0 || bestReturn<0) {
                if(reports.size>=12 && returned.size>=12) {
                    returnOffset=null
                    if(reports.first().receivedAtNs<=returned.first().receivedAtNs) {
                        val stale=reports.removeAt(0)
                        captures.remove(stale.value.sequence)
                    } else {
                        returned.removeAt(0)
                    }
                    misses+=1
                }
                break
            }

            repeat(bestReport) {
                val stale=reports.removeAt(0)
                captures.remove(stale.value.sequence)
                misses+=1
            }
            repeat(bestReturn) {
                returned.removeAt(0)
                misses+=1
            }

            val report=reports.removeAt(0).value
            val frame=returned.removeAt(0)
            val capture=captures.remove(report.sequence)
            if(capture==null) {
                misses+=1
                continue
            }
            val elapsed=frame.receivedAtNs-capture.elapsedNs
            if(elapsed<=0) {
                misses+=1
                continue
            }
            latencyMs.add(elapsed/1_000_000L)
            while(latencyMs.size>maxSamples) latencyMs.removeAt(0)
            matchedAny=true
        }
        return matchedAny
    }

    private fun calibrateReturnOffsetLocked() {
        var bestDiff=Long.MAX_VALUE
        var bestReport:PendingReport?=null
        var bestReturn:Returned?=null
        val reportLimit=minOf(8,reports.size)
        val returnLimit=minOf(8,returned.size)
        for(ri in 0 until reportLimit) {
            for(fi in 0 until returnLimit) {
                val diff=abs(reports[ri].receivedAtNs-returned[fi].receivedAtNs)
                if(diff<bestDiff) {
                    bestDiff=diff
                    bestReport=reports[ri]
                    bestReturn=returned[fi]
                }
            }
        }
        if(bestDiff<=CALIBRATION_WINDOW_NS && bestReport!=null && bestReturn!=null) {
            returnOffset=normalizeRtp(bestReport.value.rtpTimestamp-bestReturn.rtpGuess)
        }
    }

    private fun trimLocked() {
        while(captures.size>maxPending) {
            val key=captures.entries.first().key
            captures.remove(key)
            misses+=1
        }
        while(reports.size>maxPending) {
            val stale=reports.removeAt(0)
            captures.remove(stale.value.sequence)
            misses+=1
        }
        while(returned.size>maxPending) {
            returned.removeAt(0)
            misses+=1
        }
    }

    private fun snapshotLocked():FrameLatencySnapshot {
        val sorted=latencyMs.sorted()
        fun percentile(q:Double):Long? {
            if(sorted.isEmpty()) return null
            val index=((sorted.size-1)*q).toInt().coerceIn(0,sorted.size-1)
            return sorted[index]
        }
        return FrameLatencySnapshot(
            p50Ms=percentile(0.50),
            p95Ms=percentile(0.95),
            samples=latencyMs.size.toLong(),
            misses=misses,
            outOfOrder=outOfOrder,
            backendTotalMs=latestBackendTotalMs
        )
    }

    companion object {
        private const val RTP_MASK=0xFFFFFFFFL
        private const val RTP_WRAP=0x100000000L
        private const val MATCH_TOLERANCE_TICKS=4L
        private const val CALIBRATION_WINDOW_NS=250_000_000L

        fun rtpTimestamp90k(timestampNs:Long):Long {
            val micros=timestampNs/1_000L
            return normalizeRtp((micros*90L)/1_000L)
        }

        private fun normalizeRtp(value:Long):Long = value and RTP_MASK

        private fun rtpDistance(a:Long,b:Long):Long {
            var delta=(normalizeRtp(a)-normalizeRtp(b)) and RTP_MASK
            if(delta>0x7FFFFFFFL) delta-=RTP_WRAP
            return abs(delta)
        }
    }
}
