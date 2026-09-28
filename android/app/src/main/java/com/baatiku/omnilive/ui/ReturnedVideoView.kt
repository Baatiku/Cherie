package com.baatiku.omnilive.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.baatiku.omnilive.stream.ReturnedVideoBus
import org.webrtc.EglBase
import org.webrtc.RendererCommon
import org.webrtc.SurfaceViewRenderer

private object SharedRenderEgl {
    val egl:EglBase by lazy { EglBase.create() }
}

@Composable
fun ReturnedVideoView(modifier:Modifier=Modifier) {
    val holder=remember { arrayOfNulls<SurfaceViewRenderer>(1) }
    AndroidView(
        modifier=modifier,
        factory={ context ->
            SurfaceViewRenderer(context).apply {
                init(SharedRenderEgl.egl.eglBaseContext,null)
                setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT)
                setMirror(true)
                ReturnedVideoBus.addSink(this)
                holder[0]=this
            }
        }
    )
    DisposableEffect(Unit) {
        onDispose {
            holder[0]?.let {
                ReturnedVideoBus.removeSink(it)
                it.release()
            }
            holder[0]=null
        }
    }
}
