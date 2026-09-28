package com.baatiku.omnilive.stream

import android.content.Context
import com.baatiku.omnilive.net.LiveSession
import com.baatiku.omnilive.net.SessionApi
import kotlinx.coroutines.*
import org.webrtc.*

class WebRtcClient(
    private val context:Context,
    private val baseUrl:String,
    private val profileName:String,
    private val remoteSink:VideoSink? = null,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val egl = EglBase.create()
    private lateinit var factory:PeerConnectionFactory
    private var capturer:CameraVideoCapturer? = null
    private var peer:PeerConnection? = null
    private var localDescriptionReady = false
    private var session:LiveSession? = null
    private var iceServers:List<PeerConnection.IceServer> = emptyList()

    fun start() {
        scope.launch {
            session = SessionApi.create(baseUrl, profileName)
            iceServers = SessionApi.iceServers(baseUrl).map { config ->
                PeerConnection.IceServer.builder(config.urls)
                    .setUsername(config.username)
                    .setPassword(config.credential)
                    .createIceServer()
            }
            withContext(Dispatchers.Main) { startPeer() }
        }
    }

    private fun startPeer() {
        PeerConnectionFactory.initialize(PeerConnectionFactory.InitializationOptions.builder(context).createInitializationOptions())
        factory = PeerConnectionFactory.builder()
            .setVideoEncoderFactory(DefaultVideoEncoderFactory(egl.eglBaseContext,true,true))
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl.eglBaseContext))
            .createPeerConnectionFactory()

        val enumerator = Camera2Enumerator(context)
        val cameraName = enumerator.deviceNames.firstOrNull { enumerator.isFrontFacing(it) } ?: error("No front camera")
        capturer = enumerator.createCapturer(cameraName,null)
        val helper = SurfaceTextureHelper.create("OmniLiveCapture",egl.eglBaseContext)
        val source = factory.createVideoSource(false)
        capturer!!.initialize(helper,context,source.capturerObserver)
        val profile = QualityProfile.fromName(profileName)
        capturer!!.startCapture(profile.width,profile.height,profile.cameraFps)

        val localTrack = factory.createVideoTrack("omnilive-camera",source)
        val config = PeerConnection.RTCConfiguration(iceServers).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
        }

        peer = factory.createPeerConnection(config,observer()) ?: error("PeerConnection creation failed")
        peer!!.addTrack(localTrack,listOf("omnilive"))
        peer!!.createOffer(object:SimpleSdpObserver() {
            override fun onCreateSuccess(desc:SessionDescription) {
                peer!!.setLocalDescription(object:SimpleSdpObserver(){},desc)
            }
        },MediaConstraints())
    }

    private fun observer() = object:PeerConnection.Observer {
        override fun onSignalingChange(state:PeerConnection.SignalingState) {}
        override fun onIceConnectionChange(state:PeerConnection.IceConnectionState) {}
        override fun onIceConnectionReceivingChange(receiving:Boolean) {}
        override fun onIceGatheringChange(state:PeerConnection.IceGatheringState) {
            if (state == PeerConnection.IceGatheringState.COMPLETE && !localDescriptionReady) {
                localDescriptionReady = true
                val local = peer?.localDescription ?: return
                val live = session ?: return
                scope.launch {
                    val answer = SessionApi.exchangeOffer(baseUrl,live,local.description)
                    withContext(Dispatchers.Main) {
                        peer?.setRemoteDescription(object:SimpleSdpObserver(){},
                            SessionDescription(SessionDescription.Type.fromCanonicalForm(answer.first),answer.second))
                    }
                }
            }
        }
        override fun onIceCandidate(candidate:IceCandidate) {}
        override fun onIceCandidatesRemoved(candidates:Array<out IceCandidate>) {}
        override fun onAddStream(stream:MediaStream) {}
        override fun onRemoveStream(stream:MediaStream) {}
        override fun onDataChannel(channel:DataChannel) {}
        override fun onRenegotiationNeeded() {}
        override fun onAddTrack(receiver:RtpReceiver, mediaStreams:Array<out MediaStream>) {
            val track = receiver.track()
            if (track is VideoTrack && remoteSink != null) track.addSink(remoteSink)
        }
    }

    fun stop() {
        try { capturer?.stopCapture() } catch (_:InterruptedException) {}
        capturer?.dispose()
        peer?.close()
        if (::factory.isInitialized) factory.dispose()
        egl.release()
        scope.cancel()
    }
}

open class SimpleSdpObserver:SdpObserver {
    override fun onCreateSuccess(desc:SessionDescription) {}
    override fun onSetSuccess() {}
    override fun onCreateFailure(error:String) {}
    override fun onSetFailure(error:String) {}
}
