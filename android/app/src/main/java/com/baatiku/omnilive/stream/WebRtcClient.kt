package com.baatiku.omnilive.stream

import android.content.Context
import com.baatiku.omnilive.net.LiveSession
import com.baatiku.omnilive.net.SessionApi
import kotlinx.coroutines.*
import org.webrtc.*
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max

class WebRtcClient(
    private val context:Context,
    private val baseUrl:String,
    initialProfileName:String,
    private val remoteSink:VideoSink=ReturnedVideoBus,
) {
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    private val egl=EglBase.create()
    private lateinit var factory:PeerConnectionFactory
    private var capturer:CameraVideoCapturer?=null
    private var localTrack:VideoTrack?=null
    private var peer:PeerConnection?=null
    private var localDescriptionReady=false
    private var session:LiveSession?=null
    private var iceServers:List<PeerConnection.IceServer> = emptyList()
    private var statsJob:Job?=null
    private var reconnectJob:Job?=null
    private var reconnectAttempts=0
    private val stopped=AtomicBoolean(false)
    private val quality=AdaptiveQualityController(initialProfileName)
    private var activeProfileName=initialProfileName
    private var previousLost:Long?=null
    private var previousReceived:Long?=null

    fun start() {
        stopped.set(false)
        LiveTelemetry.update { it.copy(activeProfile=activeProfileName) }
        connect()
    }

    private fun connect() {
        LiveTelemetry.update { it.copy(connectionState="starting",lastError=null) }
        scope.launch {
            try {
                session=SessionApi.create(baseUrl,activeProfileName)
                iceServers=SessionApi.iceServers(baseUrl).map { cfg ->
                    PeerConnection.IceServer.builder(cfg.urls)
                        .setUsername(cfg.username)
                        .setPassword(cfg.credential)
                        .createIceServer()
                }
                withContext(Dispatchers.Main) { startPeer() }
            } catch(t:Throwable) {
                fail(t.message ?: t.javaClass.simpleName)
            }
        }
    }

    private fun ensureFactoryAndCapture() {
        if(!::factory.isInitialized) {
            PeerConnectionFactory.initialize(
                PeerConnectionFactory.InitializationOptions.builder(context).createInitializationOptions()
            )
            factory=PeerConnectionFactory.builder()
                .setVideoEncoderFactory(DefaultVideoEncoderFactory(egl.eglBaseContext,true,true))
                .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl.eglBaseContext))
                .createPeerConnectionFactory()
        }

        if(capturer==null) {
            val enumerator=Camera2Enumerator(context)
            val cameraName=enumerator.deviceNames.firstOrNull { enumerator.isFrontFacing(it) }
                ?: error("No front camera")
            capturer=enumerator.createCapturer(cameraName,null)
            val helper=SurfaceTextureHelper.create("OmniLiveCapture",egl.eglBaseContext)
            val source=factory.createVideoSource(false)
            capturer!!.initialize(helper,context,source.capturerObserver)
            val profile=QualityProfile.fromName(activeProfileName)
            capturer!!.startCapture(profile.width,profile.height,profile.cameraFps)
            localTrack=factory.createVideoTrack("omnilive-camera",source)
        }
    }

    private fun startPeer() {
        ensureFactoryAndCapture()
        localDescriptionReady=false
        peer?.close()
        peer=createPeer()
        peer!!.addTrack(localTrack!!,listOf("omnilive"))

        peer!!.createOffer(object:SimpleSdpObserver() {
            override fun onCreateSuccess(desc:SessionDescription) {
                peer?.setLocalDescription(object:SimpleSdpObserver(){},desc)
            }
            override fun onCreateFailure(error:String) { fail(error) }
        },MediaConstraints())

        statsJob?.cancel()
        statsJob=scope.launch {
            while(isActive) {
                delay(2000)
                collectStats()
            }
        }
    }

    private fun createPeer():PeerConnection {
        val config=PeerConnection.RTCConfiguration(iceServers).apply {
            sdpSemantics=PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy=PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
        }
        return factory.createPeerConnection(config,observer()) ?: error("PeerConnection creation failed")
    }

    private fun fail(message:String) {
        LiveTelemetry.update { it.copy(connectionState="failed",lastError=message) }
        scheduleReconnect()
    }

    private fun scheduleReconnect() {
        if(stopped.get() || reconnectJob?.isActive==true) return
        reconnectAttempts++
        val exponent=minOf(reconnectAttempts-1,5)
        val delayMs=minOf(30000L,1000L*(1L shl exponent))
        LiveTelemetry.update { it.copy(connectionState="reconnecting",lastError="Retry in "+delayMs+" ms") }
        reconnectJob=scope.launch {
            delay(delayMs)
            if(!stopped.get()) connect()
        }
    }

    private fun collectStats() {
        val current=peer ?: return
        current.getStats { report ->
            var rttMs:Long?=null
            var packetsLost:Long?=null
            var packetsReceived:Long?=null
            var framesReceived:Long?=null

            report.statsMap.values.forEach { stat ->
                val members=stat.members
                when(stat.type) {
                    "candidate-pair" -> {
                        val state=members["state"]?.toString()
                        val nominated=members["nominated"] as? Boolean ?: false
                        if(state=="succeeded" && nominated) {
                            val seconds=members["currentRoundTripTime"] as? Number
                            if(seconds!=null) rttMs=(seconds.toDouble()*1000.0).toLong()
                        }
                    }
                    "inbound-rtp" -> {
                        val kind=members["kind"]?.toString() ?: members["mediaType"]?.toString()
                        if(kind=="video") {
                            packetsLost=(members["packetsLost"] as? Number)?.toLong()
                            packetsReceived=(members["packetsReceived"] as? Number)?.toLong()
                            framesReceived=(members["framesReceived"] as? Number)?.toLong()
                        }
                    }
                }
            }

            LiveTelemetry.update {
                it.copy(
                    rttMs=rttMs ?: it.rttMs,
                    packetsLost=packetsLost ?: it.packetsLost,
                    framesReceived=framesReceived ?: it.framesReceived
                )
            }

            val oldLost=previousLost
            val oldReceived=previousReceived
            if(rttMs!=null && packetsLost!=null && packetsReceived!=null && oldLost!=null && oldReceived!=null) {
                val lostDelta=max(0,packetsLost-oldLost)
                val receivedDelta=max(0,packetsReceived-oldReceived)
                val total=lostDelta+receivedDelta
                val loss=if(total>0) lostDelta.toDouble()/total.toDouble() else 0.0
                val next=quality.update(rttMs!!,loss,0)
                if(next!=activeProfileName) {
                    activeProfileName=next
                    val p=QualityProfile.fromName(next)
                    capturer?.changeCaptureFormat(p.width,p.height,p.cameraFps)
                    LiveTelemetry.update { it.copy(activeProfile=next,packetLoss=loss) }
                } else {
                    LiveTelemetry.update { it.copy(packetLoss=loss) }
                }
            }
            previousLost=packetsLost
            previousReceived=packetsReceived
        }
    }

    private fun observer()=object:PeerConnection.Observer {
        override fun onSignalingChange(state:PeerConnection.SignalingState) {}
        override fun onIceConnectionChange(state:PeerConnection.IceConnectionState) {
            LiveTelemetry.update { it.copy(iceState=state.name.lowercase()) }
            if(state==PeerConnection.IceConnectionState.FAILED ||
                state==PeerConnection.IceConnectionState.DISCONNECTED) scheduleReconnect()
        }
        override fun onConnectionChange(state:PeerConnection.PeerConnectionState) {
            LiveTelemetry.update { it.copy(connectionState=state.name.lowercase()) }
            if(state==PeerConnection.PeerConnectionState.CONNECTED) {
                reconnectAttempts=0
                reconnectJob?.cancel()
                reconnectJob=null
            }
            if(state==PeerConnection.PeerConnectionState.FAILED ||
                state==PeerConnection.PeerConnectionState.DISCONNECTED) scheduleReconnect()
        }
        override fun onIceConnectionReceivingChange(receiving:Boolean) {}
        override fun onIceGatheringChange(state:PeerConnection.IceGatheringState) {
            if(state==PeerConnection.IceGatheringState.COMPLETE && !localDescriptionReady) {
                localDescriptionReady=true
                val local=peer?.localDescription ?: return
                val live=session ?: return
                scope.launch {
                    try {
                        val answer=SessionApi.exchangeOffer(baseUrl,live,local.description)
                        withContext(Dispatchers.Main) {
                            peer?.setRemoteDescription(object:SimpleSdpObserver(){
                                override fun onSetFailure(error:String) { fail(error) }
                            },SessionDescription(SessionDescription.Type.fromCanonicalForm(answer.first),answer.second))
                        }
                    } catch(t:Throwable) {
                        fail(t.message ?: t.javaClass.simpleName)
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
        override fun onAddTrack(receiver:RtpReceiver,mediaStreams:Array<out MediaStream>) {
            val track=receiver.track()
            if(track is VideoTrack) track.addSink(remoteSink)
        }
    }

    fun stop() {
        stopped.set(true)
        reconnectJob?.cancel()
        statsJob?.cancel()
        try { capturer?.stopCapture() } catch(_:InterruptedException) {}
        capturer?.dispose()
        capturer=null
        peer?.close()
        peer=null
        if(::factory.isInitialized) factory.dispose()
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
