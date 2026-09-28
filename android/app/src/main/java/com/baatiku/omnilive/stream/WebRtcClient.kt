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
    private val remoteSink:VideoSink = ReturnedVideoBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val egl = EglBase.create()
    private lateinit var factory:PeerConnectionFactory
    private var capturer:CameraVideoCapturer? = null
    private var peer:PeerConnection? = null
    private var localDescriptionReady = false
    private var session:LiveSession? = null
    private var iceServers:List<PeerConnection.IceServer> = emptyList()
    private var statsJob:Job? = null

    fun start() {
        LiveTelemetry.update { it.copy(connectionState="starting",lastError=null) }
        scope.launch {
            try {
                session = SessionApi.create(baseUrl, profileName)
                iceServers = SessionApi.iceServers(baseUrl).map { config ->
                    PeerConnection.IceServer.builder(config.urls)
                        .setUsername(config.username)
                        .setPassword(config.credential)
                        .createIceServer()
                }
                withContext(Dispatchers.Main) { startPeer() }
            } catch (t:Throwable) {
                LiveTelemetry.update { it.copy(connectionState="failed",lastError=t.message ?: t.javaClass.simpleName) }
            }
        }
    }

    private fun startPeer() {
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(context).createInitializationOptions()
        )
        factory = PeerConnectionFactory.builder()
            .setVideoEncoderFactory(DefaultVideoEncoderFactory(egl.eglBaseContext,true,true))
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl.eglBaseContext))
            .createPeerConnectionFactory()

        val enumerator = Camera2Enumerator(context)
        val cameraName = enumerator.deviceNames.firstOrNull { enumerator.isFrontFacing(it) }
            ?: error("No front camera")
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
            override fun onCreateFailure(error:String) {
                LiveTelemetry.update { it.copy(connectionState="failed",lastError=error) }
            }
        },MediaConstraints())

        statsJob = scope.launch {
            while (isActive) {
                delay(2000)
                collectStats()
            }
        }
    }

    private fun collectStats() {
        val current = peer ?: return
        current.getStats { report ->
            var rttMs:Long? = null
            var packetsLost:Long? = null
            var framesReceived:Long? = null

            report.statsMap.values.forEach { stat ->
                val members = stat.members
                when (stat.type) {
                    "candidate-pair" -> {
                        val state = members["state"]?.toString()
                        val nominated = members["nominated"] as? Boolean ?: false
                        if (state == "succeeded" && nominated) {
                            val seconds = members["currentRoundTripTime"] as? Number
                            if (seconds != null) rttMs = (seconds.toDouble() * 1000.0).toLong()
                        }
                    }
                    "inbound-rtp" -> {
                        val kind = members["kind"]?.toString() ?: members["mediaType"]?.toString()
                        if (kind == "video") {
                            packetsLost = (members["packetsLost"] as? Number)?.toLong()
                            framesReceived = (members["framesReceived"] as? Number)?.toLong()
                        }
                    }
                }
            }
            LiveTelemetry.update {
                it.copy(rttMs=rttMs ?: it.rttMs,packetsLost=packetsLost ?: it.packetsLost,framesReceived=framesReceived ?: it.framesReceived)
            }
        }
    }

    private fun observer() = object:PeerConnection.Observer {
        override fun onSignalingChange(state:PeerConnection.SignalingState) {}

        override fun onIceConnectionChange(state:PeerConnection.IceConnectionState) {
            LiveTelemetry.update { it.copy(iceState=state.name.lowercase()) }
        }

        override fun onConnectionChange(state:PeerConnection.PeerConnectionState) {
            LiveTelemetry.update { it.copy(connectionState=state.name.lowercase()) }
        }

        override fun onIceConnectionReceivingChange(receiving:Boolean) {}

        override fun onIceGatheringChange(state:PeerConnection.IceGatheringState) {
            if (state == PeerConnection.IceGatheringState.COMPLETE && !localDescriptionReady) {
                localDescriptionReady = true
                val local = peer?.localDescription ?: return
                val live = session ?: return
                scope.launch {
                    try {
                        val answer = SessionApi.exchangeOffer(baseUrl,live,local.description)
                        withContext(Dispatchers.Main) {
                            peer?.setRemoteDescription(object:SimpleSdpObserver(){
                                override fun onSetFailure(error:String) {
                                    LiveTelemetry.update { it.copy(connectionState="failed",lastError=error) }
                                }
                            }, SessionDescription(SessionDescription.Type.fromCanonicalForm(answer.first),answer.second))
                        }
                    } catch (t:Throwable) {
                        LiveTelemetry.update { it.copy(connectionState="failed",lastError=t.message ?: t.javaClass.simpleName) }
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
            if (track is VideoTrack) track.addSink(remoteSink)
        }
    }

    fun stop() {
        statsJob?.cancel()
        try { capturer?.stopCapture() } catch (_:InterruptedException) {}
        capturer?.dispose()
        peer?.close()
        if (::factory.isInitialized) factory.dispose()
        egl.release()
        scope.cancel()
        LiveTelemetry.reset()
    }
}

open class SimpleSdpObserver:SdpObserver {
    override fun onCreateSuccess(desc:SessionDescription) {}
    override fun onSetSuccess() {}
    override fun onCreateFailure(error:String) {}
    override fun onSetFailure(error:String) {}
}
