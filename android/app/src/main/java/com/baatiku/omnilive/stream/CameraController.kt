package com.baatiku.omnilive.stream

import android.annotation.SuppressLint
import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Handler

class CameraController(context:Context) {
    private val manager = context.getSystemService(CameraManager::class.java)
    fun frontCameraId():String? = manager.cameraIdList.firstOrNull { id ->
        manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
    }
    @SuppressLint("MissingPermission")
    fun openFrontCamera(callback:android.hardware.camera2.CameraDevice.StateCallback, handler:Handler?) {
        manager.openCamera(frontCameraId() ?: error("No front camera found"), callback, handler)
    }
}
