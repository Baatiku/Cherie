package com.baatiku.omnilive.rootlabbridge

import android.app.Service
import android.content.Intent
import android.os.*
import android.util.Log
import android.view.Surface
import com.baatiku.omnilive.stream.SharedEglContextRegistry
import com.baatiku.omnilive.stream.SurfaceFrameOutput
import org.webrtc.EglBase

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
        const val KEY_SURFACES="surfaces"
        private const val TAG="OmniLiveRootBroker"
    }

    private val outputs=mutableListOf<SurfaceFrameOutput>()
    private val surfaces=mutableListOf<Surface>()

    private val messenger=Messenger(Handler(Looper.getMainLooper()) { message ->
        if(!isAllowedSender(message.sendingUid)) {
            Log.w(TAG,"rejected message from uid="+message.sendingUid)
            return@Handler true
        }
        when(message.what) {
            MSG_SET_SURFACES -> {
                replaceSurfaces(readSurfaces(message.data))
                true
            }
            MSG_CLEAR_SURFACES -> {
                clearSurfaces()
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
        }
    }

    override fun onCreate() {
        super.onCreate()
        SharedEglContextRegistry.addListener(eglListener)
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
        clearSurfaces()
        Log.i(TAG,"surface broker destroyed")
        super.onDestroy()
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
