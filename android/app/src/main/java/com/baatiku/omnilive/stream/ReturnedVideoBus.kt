package com.baatiku.omnilive.stream

import org.webrtc.VideoFrame
import org.webrtc.VideoSink

/**
 * Compatibility facade for returned WebRTC video.
 *
 * New outputs should attach through ProcessedFrameOutput / addConsumer(). The normal preview keeps
 * using addSink() so the rooted compatibility layer never needs to enter WebRTC transport code.
 */
object ReturnedVideoBus : VideoSink {
    fun addSink(sink:VideoSink) = ProcessedFrameOutput.addVideoSink(sink)

    fun removeSink(sink:VideoSink) = ProcessedFrameOutput.removeVideoSink(sink)

    fun addConsumer(consumer:ProcessedFrameConsumer) = ProcessedFrameOutput.addConsumer(consumer)

    fun removeConsumer(consumer:ProcessedFrameConsumer) = ProcessedFrameOutput.removeConsumer(consumer)

    fun setStreamAvailable(available:Boolean) = ProcessedFrameOutput.setStreamAvailable(available)

    val isStreamAvailable:Boolean
        get()=ProcessedFrameOutput.isStreamAvailable

    override fun onFrame(frame:VideoFrame) {
        ProcessedFrameOutput.onFrame(frame)
    }
}
