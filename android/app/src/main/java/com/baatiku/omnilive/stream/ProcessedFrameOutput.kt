package com.baatiku.omnilive.stream

import org.webrtc.VideoFrame
import org.webrtc.VideoSink
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicBoolean

data class ProcessedFrameInfo(
    val width:Int,
    val height:Int,
    val rotation:Int,
    val timestampNs:Long
)

interface ProcessedFrameConsumer {
    /**
     * The frame is valid only for this callback. Consumers that hand it to asynchronous work
     * must call retain() before returning and release() when that work completes.
     */
    fun onProcessedFrame(frame:VideoFrame,info:ProcessedFrameInfo)
    fun onStreamAvailabilityChanged(available:Boolean) {}
}

object ProcessedFrameOutput : VideoSink {
    private val videoSinks=CopyOnWriteArraySet<VideoSink>()
    private val consumers=CopyOnWriteArraySet<ProcessedFrameConsumer>()
    private val streamAvailable=AtomicBoolean(false)

    val isStreamAvailable:Boolean
        get()=streamAvailable.get()

    fun addVideoSink(sink:VideoSink) {
        videoSinks.add(sink)
    }

    fun removeVideoSink(sink:VideoSink) {
        videoSinks.remove(sink)
    }

    fun addConsumer(consumer:ProcessedFrameConsumer) {
        consumers.add(consumer)
        consumer.onStreamAvailabilityChanged(streamAvailable.get())
    }

    fun removeConsumer(consumer:ProcessedFrameConsumer) {
        consumers.remove(consumer)
    }

    fun setStreamAvailable(available:Boolean) {
        if(streamAvailable.getAndSet(available)==available) return
        consumers.forEach { consumer ->
            try {
                consumer.onStreamAvailabilityChanged(available)
            } catch(_:Throwable) {
                // One optional output must never break the live preview or other outputs.
            }
        }
    }

    override fun onFrame(frame:VideoFrame) {
        if(!streamAvailable.get()) setStreamAvailable(true)
        val info=ProcessedFrameInfo(
            width=frame.rotatedWidth,
            height=frame.rotatedHeight,
            rotation=frame.rotation,
            timestampNs=frame.timestampNs
        )

        videoSinks.forEach { sink ->
            frame.retain()
            try {
                sink.onFrame(frame)
            } catch(_:Throwable) {
                // Output consumers are isolated from each other.
            } finally {
                frame.release()
            }
        }

        consumers.forEach { consumer ->
            frame.retain()
            try {
                consumer.onProcessedFrame(frame,info)
            } catch(_:Throwable) {
                // Future Surface/rooted lab outputs cannot destabilize normal WebRTC rendering.
            } finally {
                frame.release()
            }
        }
    }
}
