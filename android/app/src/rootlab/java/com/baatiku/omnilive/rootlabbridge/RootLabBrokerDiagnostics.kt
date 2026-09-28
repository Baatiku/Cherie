package com.baatiku.omnilive.rootlabbridge

import org.json.JSONObject

/**
 * Rootlab-only process-local diagnostics for phone-side validation.
 *
 * The normal app reads this class reflectively, so non-root builds retain no dependency on rootlab.
 */
object RootLabBrokerDiagnostics {
    private val lock=Any()

    private var targetConnected=false
    private var brokerReady=false
    private var eglReady=false
    private var streamReady=false
    private var requestedSurfaces=0
    private var attachedSurfaces=0
    private var presentedFrames=0L

    private var managerHooks=0
    private var sessionHooks=0
    private var closeHooks=0
    private var cameraOpenRequests=0
    private var sessionRequests=0
    private var closeEvents=0
    private var lastCameraId=""
    private var lastTargetSurfaceCount=0
    private var lastHookError=""

    @JvmStatic
    fun updateTargetConnected(connected:Boolean) {
        synchronized(lock) { targetConnected=connected }
    }

    @JvmStatic
    fun updateBrokerState(ready:Boolean,egl:Boolean,stream:Boolean) {
        synchronized(lock) {
            brokerReady=ready
            eglReady=egl
            streamReady=stream
        }
    }

    @JvmStatic
    fun updateSurfaceState(requested:Int,attached:Int) {
        synchronized(lock) {
            requestedSurfaces=requested.coerceAtLeast(0)
            attachedSurfaces=attached.coerceAtLeast(0)
        }
    }

    @JvmStatic
    fun recordFramePresented() {
        synchronized(lock) { presentedFrames++ }
    }

    @JvmStatic
    fun updateTargetState(
        manager:Int,
        session:Int,
        close:Int,
        opens:Int,
        sessions:Int,
        closes:Int,
        cameraId:String?,
        surfaceCount:Int,
        hookError:String?
    ) {
        synchronized(lock) {
            managerHooks=manager.coerceAtLeast(0)
            sessionHooks=session.coerceAtLeast(0)
            closeHooks=close.coerceAtLeast(0)
            cameraOpenRequests=opens.coerceAtLeast(0)
            sessionRequests=sessions.coerceAtLeast(0)
            closeEvents=closes.coerceAtLeast(0)
            lastCameraId=cameraId.orEmpty()
            lastTargetSurfaceCount=surfaceCount.coerceAtLeast(0)
            lastHookError=hookError.orEmpty()
        }
    }

    @JvmStatic
    fun resetSurfaceState() {
        synchronized(lock) {
            requestedSurfaces=0
            attachedSurfaces=0
        }
    }

    @JvmStatic
    fun snapshotJson():String=synchronized(lock) {
        JSONObject()
            .put("target_connected",targetConnected)
            .put("broker_ready",brokerReady)
            .put("egl_ready",eglReady)
            .put("stream_ready",streamReady)
            .put("requested_surfaces",requestedSurfaces)
            .put("attached_surfaces",attachedSurfaces)
            .put("presented_frames",presentedFrames)
            .put("manager_hooks",managerHooks)
            .put("session_hooks",sessionHooks)
            .put("close_hooks",closeHooks)
            .put("camera_open_requests",cameraOpenRequests)
            .put("session_requests",sessionRequests)
            .put("close_events",closeEvents)
            .put("last_camera_id",lastCameraId)
            .put("last_target_surface_count",lastTargetSurfaceCount)
            .put("last_hook_error",lastHookError)
            .toString()
    }

    @JvmStatic
    fun summaryText():String=synchronized(lock) {
        buildString {
            append(if(targetConnected) "Zoom connected" else "Zoom not connected")
            append(" · broker ")
            append(if(brokerReady) "ready" else "not ready")
            append(" · EGL ")
            append(if(eglReady) "yes" else "no")
            append(" · stream ")
            append(if(streamReady) "yes" else "no")
            append(" · surfaces ")
            append(attachedSurfaces).append("/").append(requestedSurfaces)
            append(" · presented ").append(presentedFrames)
            append(" · hooks ")
            append(managerHooks).append("/").append(sessionHooks).append("/").append(closeHooks)
            if(lastHookError.isNotBlank()) append(" · hook error: ").append(lastHookError)
        }
    }
}
