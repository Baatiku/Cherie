package com.baatiku.omnilive.stream

import org.webrtc.EglBase
import java.util.concurrent.CopyOnWriteArraySet

/**
 * Generic stream-layer registry for outputs that must share the WebRTC EGL context.
 *
 * Normal OmniLive does not depend on any rooted code. Optional outputs can subscribe without
 * reaching into WebRtcClient internals, and are notified when the active client releases EGL.
 */
object SharedEglContextRegistry {
    private val listeners=CopyOnWriteArraySet<(EglBase.Context?)->Unit>()

    @Volatile
    private var active:EglBase.Context?=null

    val current:EglBase.Context?
        get()=active

    fun publish(context:EglBase.Context) {
        active=context
        listeners.forEach { listener ->
            try { listener(context) } catch(_:Throwable) {}
        }
    }

    fun clear(context:EglBase.Context) {
        if(active!==context) return
        active=null
        listeners.forEach { listener ->
            try { listener(null) } catch(_:Throwable) {}
        }
    }

    fun addListener(listener:(EglBase.Context?)->Unit) {
        listeners.add(listener)
        try { listener(active) } catch(_:Throwable) {}
    }

    fun removeListener(listener:(EglBase.Context?)->Unit) {
        listeners.remove(listener)
    }
}
