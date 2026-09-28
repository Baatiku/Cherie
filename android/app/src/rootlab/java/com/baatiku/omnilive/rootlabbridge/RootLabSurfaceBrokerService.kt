package com.baatiku.omnilive.rootlabbridge

import android.app.Service
import android.content.Intent
import android.os.*
import android.util.Log
import android.view.Surface
import com.baatiku.omnilive.stream.ProcessedFrameConsumer
import com.baatiku.omnilive.stream.ProcessedFrameInfo
import com.baatiku.omnilive.stream.ReturnedVideoBus
import com.baatiku.omnilive.stream.SharedEglContextRegistry
import com.baatiku.omnilive.stream.SurfaceFrameOutput
import org.webrtc.EglBase
import org.webrtc.VideoFrame

/**
 * Rootlab-only broker that receives target-process Camera2 output Surfaces over Messenger and
 * attempts to render OmniLive returned frames into them with the same EGL context as WebRTC.
 *
 * It is intentionally fail-open during the first device phase: Xposed continues the physical
 * Camera2 call until surface-producer behavior is proven on the rooted target device.
 */
class RootLabSurfaceBrokerService:Service() {
    companion object {
        const val TARGET_PACKAGE="us.zoom.videomeetings"
        const val MSG_SET_SURFACES=1
        const val MSG_CLEAR_SURFACES=2
        const val MSG_QUERY_STATE=3
        const val MSG_STATE=4
        const val KEY_SURFACES="surfaces"
        const val KEY_READY="ready"
        const val KEY_EGL_READY="egl_ready"
        const val KEY_STREAM_READY="stream_ready"
        private const val TAG="OmniLiveRootBroker"
    }

    private val outputs=mutableListOf<SurfaceFrameOutput>()
    private val surfaces=mutableListOf<Surface>()
    private var client:Messenger?=null

    private val streamListener=object:ProcessedFrameConsumer {
        override fun onProcessedFrame(frame:VideoFrame,info:ProcessedFrameInfo) = Unit
        override fun onStreamAvailabilityChanged(available:Boolean) {
            publishState()
        }
    }

    private val messenger=Messenger(Handler(Looper.getMainLooper()) { message ->
        if(!isAllowedSender(message.sendingUid)) {
            Log.w(TAG,"rejected message from uid="+message.sendingUid)
            return@Handler true
        }
        message.replyTo?.let { client=it }
        when(message.what) {
            MSG_SET_SURFACES -> {
                replaceSurfaces(readSurfaces(message.data))
                true
            }
            MSG_CLEAR_SURFACES -> {
                clearSurfaces()
                publishState()
                true
            }
            MSG_QUERY_STATE -> {
                publishState()
                true
            }
            else -> false
        }
    })

    private val eglListener:(EglBase.Context?)->Unit={ context ->
        Handler(Looper.getMainLooper()).post {
            if(context==null) {
                detachOutputs()
                Log.i(TAG,"WebRTC EGL unavailable; retaining "+surfaces.size+" pending surface(s)")
            } else {
                attachPending(context)
            }
            publishState()
        }
    }

    override fun onCreate() {
        super.onCreate()
        SharedEglContextRegistry.addListener(eglListener)
        ReturnedVideoBus.addConsumer(streamListener)
        Log.i(TAG,"surface broker created")
    }

    override fun onBind(intent:Intent?):IBinder {
        Log.i(TAG,"bind requested uid="+Binder.getCallingUid())
        return messenger.binder
    }

    override fun onUnbind(intent:Intent?):Boolean {
        clearSurfaces()
        return false
    }

    override fun onDestroy() {
        SharedEglContextRegistry.removeListener(eglListener)
        ReturnedVideoBus.removeConsumer(streamListener)
        client=null
        clearSurfaces()
        Log.i(TAG,"surface broker destroyed")
        super.onDestroy()
    }

    private fun publishState() {
        val target=client ?: return
        val eglReady=SharedEglContextRegistry.current!=null
        val streamReady=ReturnedVideoBus.isStreamAvailable
        val ready=eglReady && streamReady
        try {
            val message=Message.obtain(null,MSG_STATE)
            message.data=Bundle().apply {
                putBoolean(KEY_READY,ready)
                putBoolean(KEY_EGL_READY,eglReady)
                putBoolean(KEY_STREAM_READY,streamReady)
            }
            target.send(message)
            Log.i(TAG,"broker state ready="+ready+" egl="+eglReady+" stream="+streamReady)
        } catch(t:Throwable) {
            Log.w(TAG,"failed to publish broker state",t)
            client=null
        }
    }

    private fun isAllowedSender(uid:Int):Boolean {
        if(uid<=0) return false
        val packages=packageManager.getPackagesForUid(uid) ?: return false
        return packages.any { it==TARGET_PACKAGE }
    }

    @Suppress("DEPRECATION")
    private fun readSurfaces(bundle:Bundle?):List<Surface> {
        if(bundle==null) return emptyList()
        val received=if(Build.VERSION.SDK_INT>=33) {
            bundle.getParcelableArrayList(KEY_SURFACES,Surface::class.java)
        } else {
            bundle.getParcelableArrayList(KEY_SURFACES)
        } ?: return emptyList()
        return received.filter { it.isValid }
    }

    private fun replaceSurfaces(next:List<Surface>) {
        detachOutputs()
        releaseSurfaces()
        surfaces.addAll(next)
        Log.i(TAG,"received "+surfaces.size+" target surface(s)")
        SharedEglContextRegistry.current?.let { attachPending(it) }
            ?: Log.i(TAG,"waiting for WebRTC EGL context before attach")
        publishState()
    }

    private fun attachPending(context:EglBase.Context) {
        detachOutputs()
        if(surfaces.isEmpty()) return
        var attached=0
        surfaces.forEachIndexed { index,surface ->
            if(!surface.isValid) return@forEachIndexed
            val output=SurfaceFrameOutput(context)
            try {
                output.attach(surface,mirror=false)
                outputs.add(output)
                attached++
                Log.i(TAG,"attached target surface index="+index)
            } catch(t:Throwable) {
                try { output.close() } catch(_:Throwable) {}
                Log.w(TAG,"target surface attach failed index="+index+": "+(t.message ?: t.javaClass.simpleName))
            }
        }
        Log.i(TAG,"surface attach summary attached="+attached+" requested="+surfaces.size)
    }

    private fun detachOutputs() {
        outputs.forEach { output ->
            try { output.close() } catch(_:Throwable) {}
        }
        outputs.clear()
    }

    private fun releaseSurfaces() {
        surfaces.forEach { surface ->
            try { surface.release() } catch(_:Throwable) {}
        }
        surfaces.clear()
    }

    private fun clearSurfaces() {
        detachOutputs()
        releaseSurfaces()
        Log.i(TAG,"target surfaces cleared")
    }
}
