package com.baatiku.omnilive.stream

import org.webrtc.VideoFrame
import org.webrtc.VideoSink
import java.util.concurrent.CopyOnWriteArraySet

/**
 * Fan-out for the device camera track used only by the in-app local preview.
 * It does not alter capture, encoding, signalling, or the returned processed stream.
 */
object CameraPreviewBus : VideoSink {
    private val sinks=CopyOnWriteArraySet<VideoSink>()

    fun addSink(sink:VideoSink) {
        sinks.add(sink)
    }

    fun removeSink(sink:VideoSink) {
        sinks.remove(sink)
    }

    override fun onFrame(frame:VideoFrame) {
        sinks.forEach { sink ->
            try { sink.onFrame(frame) } catch(_:Throwable) {}
        }
    }
}
