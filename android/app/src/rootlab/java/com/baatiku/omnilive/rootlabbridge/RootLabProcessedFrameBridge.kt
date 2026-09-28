package com.baatiku.omnilive.rootlabbridge

import com.baatiku.omnilive.rootlab.CompatibilitySessionController
import com.baatiku.omnilive.rootlab.LabFrameEndpoint
import com.baatiku.omnilive.rootlab.LabFrameInfo
import com.baatiku.omnilive.stream.ProcessedFrameConsumer
import com.baatiku.omnilive.stream.ProcessedFrameInfo
import com.baatiku.omnilive.stream.ReturnedVideoBus
import org.webrtc.VideoFrame
import java.io.Closeable
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Lab-only metadata/lifecycle bridge.
 *
 * This class is compiled only when -Pomnilive.rootLab=true. It does not intercept camera APIs or
 * modify another app. Pixel delivery remains on SurfaceFrameOutput; this bridge only forwards
 * frame metadata and output availability into the isolated lab controller/endpoint.
 */
class RootLabProcessedFrameBridge(
    private val controller:CompatibilitySessionController,
    private val endpoint:LabFrameEndpoint
):ProcessedFrameConsumer,Closeable {
    private val started=AtomicBoolean(false)

    fun start() {
        if(started.compareAndSet(false,true)) {
            ReturnedVideoBus.addConsumer(this)
        }
    }

    override fun onProcessedFrame(frame:VideoFrame,info:ProcessedFrameInfo) {
        if(!started.get()) return
        endpoint.onFrameAvailable(
            LabFrameInfo(
                width=info.width,
                height=info.height,
                rotation=info.rotation,
                timestampNs=info.timestampNs
            )
        )
    }

    override fun onStreamAvailabilityChanged(available:Boolean) {
        if(!started.get()) return
        endpoint.onOutputAvailabilityChanged(available)
        controller.onOutputAvailabilityChanged(available)
    }

    override fun close() {
        if(started.compareAndSet(true,false)) {
            ReturnedVideoBus.removeConsumer(this)
            endpoint.onOutputAvailabilityChanged(false)
            controller.onOutputAvailabilityChanged(false)
        }
    }
}
