package com.baatiku.omnilive.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.baatiku.omnilive.stream.CameraPreviewBus
import com.baatiku.omnilive.stream.SharedEglContextRegistry
import org.webrtc.EglBase
import org.webrtc.RendererCommon
import org.webrtc.SurfaceViewRenderer

private object PreviewFallbackEgl {
    val egl:EglBase by lazy { EglBase.create() }
}

@Composable
fun CameraPreviewView(modifier:Modifier=Modifier) {
    val sharedContext=SharedEglContextRegistry.current ?: PreviewFallbackEgl.egl.eglBaseContext
    val holder=remember(sharedContext) { arrayOfNulls<SurfaceViewRenderer>(1) }

    AndroidView(
        modifier=modifier,
        factory={ context ->
            SurfaceViewRenderer(context).apply {
                init(sharedContext,null)
                setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FILL)
                setMirror(true)
                setEnableHardwareScaler(true)
                CameraPreviewBus.addSink(this)
                holder[0]=this
            }
        }
    )

    DisposableEffect(sharedContext) {
        onDispose {
            holder[0]?.let {
                CameraPreviewBus.removeSink(it)
                it.release()
            }
            holder[0]=null
        }
    }
}
