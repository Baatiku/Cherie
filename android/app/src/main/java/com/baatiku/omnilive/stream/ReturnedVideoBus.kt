package com.baatiku.omnilive.stream

import org.webrtc.VideoFrame
import org.webrtc.VideoSink
import java.util.concurrent.CopyOnWriteArraySet

object ReturnedVideoBus : VideoSink {
    private val sinks = CopyOnWriteArraySet<VideoSink>()

    fun addSink(sink: VideoSink) {
        sinks.add(sink)
    }

    fun removeSink(sink: VideoSink) {
        sinks.remove(sink)
    }

    override fun onFrame(frame: VideoFrame) {
        frame.retain()
        try {
            sinks.forEach { it.onFrame(frame) }
        } finally {
            frame.release()
        }
    }
}
