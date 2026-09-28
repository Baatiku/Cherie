package com.baatiku.omnilive.stream

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import com.baatiku.omnilive.net.LiveSession
import com.baatiku.omnilive.net.SessionApi
import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import org.webrtc.*
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max

class WebRtcClient(
    private val context:Context,
    private val baseUrl:String,
    initialProfileName:String,
    private val effectName:String,
    private val remoteSink:VideoSink=ReturnedVideoBus,
) {
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    private val egl=EglBase.create()
    private val connectivity=context.getSystemService(ConnectivityManager::class.java)
    private val httpClient=OkHttpClient.Builder().pingInterval(20,TimeUnit.SECONDS).build()
    private lateinit var factory:PeerConnectionFactory
    private var capturer:CameraVideoCapturer?=null
    private var localTrack:VideoTrack?=null
    private var peer:PeerConnection?=null
    @Volatile private var liveSocket:WebSocket?=null
    private var localDescriptionReady=false
    private var session:LiveSession?=null
    private var iceServers:List<PeerConnection.IceServer> = emptyList()
    private var statsJob:Job?=null
    private var reconnectJob:Job?=null
    private var connectJob:Job?=null
    private var reconnectAttempts=0
    private val stopped=AtomicBoolean(false)
    private val quality=AdaptiveQualityController(initialProfileName)
    private var activeProfileName=initialProfileName
    private var previousLost:Long?=null
    private var previousReceived:Long?=null
    private var currentNetwork:Network?=null
    private var hasSeenNetwork=false
    private var networkCallbackRegistered=false
    @Volatile private var backendP95Ms:Long=0

    private val networkCallback=object:ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network:Network) {
            val previous=currentNetwork
            val firstNetwork=!hasSeenNetwork
            hasSeenNetwork=true
            currentNetwork=network
            LiveTelemetry.update { it.copy(networkState="available") }
            if(!firstNetwork && previous!=network && !stopped.get()) reconnectForNetworkChange()
        }

        override fun onLost(network:Network) {
            if(currentNetwork==network) {
                currentNetwork=null
                LiveTelemetry.update { it.copy(networkState="offline") }
                scheduleReconnect()
            }
        }

        override fun onCapabilitiesChanged(network:Network,capabilities:NetworkCapabilities) {
            if(network!=currentNetwork) return
            val label=when {
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                else -> "available"
            }
            LiveTelemetry.update { it.copy(networkState=label) }
        }
    }

    fun start() {
        stopped.set(false)
        LiveTelemetry.update { it.copy(activeProfile=activeProfileName,activeEffect=effectName) }
        if(!networkCallbackRegistered) {
            connectivity.registerDefaultNetworkCallback(networkCallback)
            networkCallbackRegistered=true
        }
        connect()
    }

    private fun connect() {
        if(stopped.get()) return
        connectJob?.cancel()
        closeLiveSocket("reconnect")
        LiveTelemetry.update { it.copy(connectionState="starting",lastError=null) }
        connectJob=scope.launch {
            try {
                session=null
                iceServers=SessionApi.iceServers(baseUrl).map { cfg ->
                    PeerConnection.IceServer.builder(cfg.urls)
                        .setUsername(cfg.username)
                        .setPassword(cfg.credential)
                        .createIceServer()
                }
                withContext(Dispatchers.Main) { startPeer() }
            } catch(t:Throwable) {
                if(t is CancellationException) throw t
                fail(t.message ?: t.javaClass.simpleName)
            }
        }
    }

    private fun reconnectForNetworkChange() {
        reconnectJob?.cancel()
        reconnectJob=null
        reconnectAttempts=0
        previousLost=null
        previousReceived=null
        LiveTelemetry.update { it.copy(connectionState="network_switch",lastError=null) }
        connect()
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
            val p=QualityProfile.fromName(activeProfileName)
            capturer!!.startCapture(p.width,p.height,p.cameraFps)
            localTrack=factory.createVideoTrack("omnilive-camera",source)
        }
    }

    private fun startPeer() {
        if(stopped.get()) return
        ensureFactoryAndCapture()
        localDescriptionReady=false
        previousLost=null
        previousReceived=null
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

    private fun liveWebSocketUrl():String {
        val base=baseUrl.trimEnd('/')
        val websocketBase=when {
            base.startsWith("https://") -> "wss://"+base.removePrefix("https://")
            base.startsWith("http://") -> "ws://"+base.removePrefix("http://")
            base.startsWith("wss://") || base.startsWith("ws://") -> base
            else -> "ws://"+base
        }
        return websocketBase+"/v1/live"
    }

    private fun applyBackendTelemetry(backend:com.baatiku.omnilive.net.BackendTelemetry) {
        backendP95Ms=backend.processorP95Ms
        LiveTelemetry.update {
            it.copy(
                backendProcessorP95Ms=backend.processorP95Ms,
                backendDetectorP95Ms=backend.detectorP95Ms,
                backendModelP95Ms=backend.modelP95Ms,
                targetNeuralFps=backend.targetNeuralFps,
                droppedInferenceFrames=backend.droppedInferenceFrames,
                inputOverwrittenFrames=backend.inputOverwrittenFrames
            )
        }
    }

    private fun openLiveSocket(sdp:String) {
        if(stopped.get()) return
        closeLiveSocket("replace")
        val request=Request.Builder().url(liveWebSocketUrl()).build()
        val listener=object:WebSocketListener() {
            override fun onOpen(webSocket:WebSocket,response:Response) {
                if(stopped.get()) {
                    webSocket.close(1000,"stopped")
                    return
                }
                LiveTelemetry.update { it.copy(connectionState="signaling",lastError=null) }
                val message=JSONObject()
                    .put("action","start")
                    .put("profile",activeProfileName)
                    .put("effect",effectName)
                    .put("type","offer")
                    .put("sdp",sdp)
                webSocket.send(message.toString())
            }

            override fun onMessage(webSocket:WebSocket,text:String) {
                if(webSocket!==liveSocket || stopped.get()) return
                try {
                    val json=JSONObject(text)
                    when(json.optString("event")) {
                        "answer" -> {
                            session=LiveSession(
                                json.getString("session_id"),
                                json.getString("token"),
                                json.getLong("expires_at")
                            )
                            val answerSdp=json.getString("sdp")
                            val answerType=json.optString("type","answer")
                            scope.launch(Dispatchers.Main) {
                                peer?.setRemoteDescription(object:SimpleSdpObserver(){
                                    override fun onSetFailure(error:String) { fail(error) }
                                },SessionDescription(SessionDescription.Type.fromCanonicalForm(answerType),answerSdp))
                            }
                        }
                        "telemetry","pong" -> {
                            json.optJSONObject("telemetry")?.let {
                                applyBackendTelemetry(SessionApi.parseTelemetry(it))
                            }
                        }
                        "expired" -> {
                            fail("Live session expired")
                        }
                        "error" -> {
                            fail(json.optString("detail","Live signalling failed"))
                        }
                    }
                } catch(t:Throwable) {
                    fail(t.message ?: "Invalid live signalling message")
                }
            }

            override fun onFailure(webSocket:WebSocket,t:Throwable,response:Response?) {
                if(webSocket!==liveSocket || stopped.get()) return
                liveSocket=null
                fail(t.message ?: "Live signalling socket failed")
            }

            override fun onClosed(webSocket:WebSocket,code:Int,reason:String) {
                if(webSocket!==liveSocket || stopped.get()) return
                liveSocket=null
                scheduleReconnect()
            }
        }
        liveSocket=httpClient.newWebSocket(request,listener)
    }

    private fun closeLiveSocket(reason:String) {
        val socket=liveSocket ?: return
        liveSocket=null
        try { socket.send(JSONObject().put("action","stop").toString()) } catch(_:Throwable) {}
        try { socket.close(1000,reason) } catch(_:Throwable) {}
    }

    private fun createPeer():PeerConnection {
        val config=PeerConnection.RTCConfiguration(iceServers).apply {
            sdpSemantics=PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy=PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
        }
        return factory.createPeerConnection(config,observer()) ?: error("PeerConnection creation failed")
    }

    private fun fail(message:String) {
        if(stopped.get()) return
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
                it.copy(rttMs=rttMs ?: it.rttMs,packetsLost=packetsLost ?: it.packetsLost,framesReceived=framesReceived ?: it.framesReceived)
            }
            val oldLost=previousLost
            val oldReceived=previousReceived
            if(rttMs!=null && packetsLost!=null && packetsReceived!=null && oldLost!=null && oldReceived!=null) {
                val lostDelta=max(0,packetsLost-oldLost)
                val receivedDelta=max(0,packetsReceived-oldReceived)
                val total=lostDelta+receivedDelta
                val loss=if(total>0) lostDelta.toDouble()/total.toDouble() else 0.0
                val next=quality.update(rttMs!!,loss,backendP95Ms)
                if(next!=activeProfileName) {
                    activeProfileName=next
                    val p=QualityProfile.fromName(next)
                    capturer?.changeCaptureFormat(p.width,p.height,p.cameraFps)
                    LiveTelemetry.update { it.copy(activeProfile=next,packetLoss=loss) }
                } else LiveTelemetry.update { it.copy(packetLoss=loss) }
            }
            previousLost=packetsLost
            previousReceived=packetsReceived
        }
    }

    private fun observer()=object:PeerConnection.Observer {
        override fun onSignalingChange(state:PeerConnection.SignalingState) {}
        override fun onIceConnectionChange(state:PeerConnection.IceConnectionState) {
            LiveTelemetry.update { it.copy(iceState=state.name.lowercase()) }
            if(state==PeerConnection.IceConnectionState.FAILED || state==PeerConnection.IceConnectionState.DISCONNECTED) scheduleReconnect()
        }
        override fun onConnectionChange(state:PeerConnection.PeerConnectionState) {
            LiveTelemetry.update { it.copy(connectionState=state.name.lowercase()) }
            if(state==PeerConnection.PeerConnectionState.CONNECTED) {
                reconnectAttempts=0
                reconnectJob?.cancel()
                reconnectJob=null
            }
            if(state==PeerConnection.PeerConnectionState.FAILED || state==PeerConnection.PeerConnectionState.DISCONNECTED) scheduleReconnect()
        }
        override fun onIceConnectionReceivingChange(receiving:Boolean) {}
        override fun onIceGatheringChange(state:PeerConnection.IceGatheringState) {
            if(state==PeerConnection.IceGatheringState.COMPLETE && !localDescriptionReady) {
                localDescriptionReady=true
                val local=peer?.localDescription ?: return
                openLiveSocket(local.description)
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
        connectJob?.cancel()
        reconnectJob?.cancel()
        statsJob?.cancel()
        closeLiveSocket("stopped")
        if(networkCallbackRegistered) {
            try { connectivity.unregisterNetworkCallback(networkCallback) } catch(_:Throwable) {}
            networkCallbackRegistered=false
        }
        currentNetwork=null
        hasSeenNetwork=false
        try { capturer?.stopCapture() } catch(_:InterruptedException) {}
        capturer?.dispose()
        capturer=null
        peer?.close()
        peer=null
        if(::factory.isInitialized) factory.dispose()
        httpClient.connectionPool.evictAll()
        httpClient.dispatcher.executorService.shutdown()
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
