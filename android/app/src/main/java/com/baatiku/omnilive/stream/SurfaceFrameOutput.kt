package com.baatiku.omnilive.stream

import android.view.Surface
import org.webrtc.EglBase
import org.webrtc.EglRenderer
import org.webrtc.GlRectDrawer
import org.webrtc.VideoFrame
import java.io.Closeable
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Generic GPU-friendly output that renders returned OmniLive frames into an Android Surface.
 *
 * EglRenderer itself keeps only one pending frame and replaces it when a fresher frame arrives,
 * preserving OmniLive's latest-frame-wins policy. This class contains no root or target-app logic.
 */
class SurfaceFrameOutput(
    sharedContext:EglBase.Context,
    private val onSwapBuffers:((Long)->Unit)?=null
):ProcessedFrameConsumer,Closeable {
    private val renderer=EglRenderer("OmniLiveSurfaceOutput")
    private val attached=AtomicBoolean(false)
    private val closed=AtomicBoolean(false)

    @Volatile var streamAvailable:Boolean=false
        private set

    @Volatile var lastFrameInfo:ProcessedFrameInfo?=null
        private set

    init {
        renderer.init(
            sharedContext,
            EglBase.CONFIG_PLAIN,
            GlRectDrawer(),
            true
        )
        onSwapBuffers?.let { callback ->
            renderer.addRenderListener { swapTimeNs -> callback(swapTimeNs) }
        }
    }

    fun attach(surface:Surface,mirror:Boolean=false) {
        check(!closed.get()) { "SurfaceFrameOutput is closed" }
        renderer.setMirror(mirror)
        renderer.createEglSurface(surface)
        if(attached.compareAndSet(false,true)) {
            ReturnedVideoBus.addConsumer(this)
        }
    }

    fun detach(onDetached:(()->Unit)?=null) {
        if(attached.compareAndSet(true,false)) {
            ReturnedVideoBus.removeConsumer(this)
        }
        renderer.releaseEglSurface {
            onDetached?.invoke()
        }
    }

    override fun onProcessedFrame(frame:VideoFrame,info:ProcessedFrameInfo) {
        if(!attached.get() || closed.get()) return
        lastFrameInfo=info
        renderer.onFrame(frame)
    }

    override fun onStreamAvailabilityChanged(available:Boolean) {
        streamAvailable=available
        if(!available && attached.get() && !closed.get()) {
            renderer.clearImage()
        }
    }

    override fun close() {
        if(!closed.compareAndSet(false,true)) return
        if(attached.compareAndSet(true,false)) {
            ReturnedVideoBus.removeConsumer(this)
        }
        renderer.release()
        lastFrameInfo=null
        streamAvailable=false
    }
}
