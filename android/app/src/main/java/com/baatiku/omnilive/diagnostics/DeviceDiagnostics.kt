package com.baatiku.omnilive.diagnostics

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import com.baatiku.omnilive.BuildConfig

data class DeviceSnapshot(
    val manufacturer:String,
    val model:String,
    val apiLevel:Int,
    val appVersion:String,
    val buildCommit:String,
    val rootLabEnabled:Boolean,
    val cameraGranted:Boolean
) {
    fun asText(backendUrl:String,referenceSelected:Boolean):String=buildString {
        appendLine("OmniLive diagnostics")
        appendLine("App: $appVersion")
        appendLine("Build: $buildCommit")
        appendLine("Device: $manufacturer $model")
        appendLine("Android API: $apiLevel")
        appendLine("Camera permission: "+if(cameraGranted) "granted" else "missing")
        appendLine("Root lab compiled: "+if(rootLabEnabled) "yes" else "no")
        appendLine("Reference image: "+if(referenceSelected) "selected" else "none")
        appendLine("Backend: "+backendUrl.ifBlank { "not configured" })
    }.trim()
}

object DeviceDiagnostics {
    fun snapshot(context:Context):DeviceSnapshot {
        return DeviceSnapshot(
            manufacturer=Build.MANUFACTURER.ifBlank { "unknown" },
            model=Build.MODEL.ifBlank { "unknown" },
            apiLevel=Build.VERSION.SDK_INT,
            appVersion=BuildConfig.VERSION_NAME,
            buildCommit=BuildConfig.BUILD_COMMIT,
            rootLabEnabled=BuildConfig.ROOT_LAB_ENABLED,
            cameraGranted=ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.CAMERA
            )==PackageManager.PERMISSION_GRANTED
        )
    }
}
