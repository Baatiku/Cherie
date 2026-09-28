package com.baatiku.omnilive.net

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

data class TransportPreflightResult(
    val health:BackendHealth,
    val iceServerCount:Int,
    val relayConfigured:Boolean,
    val sessionValidated:Boolean,
    val websocketOk:Boolean,
    val credentialMode:String
) {
    fun summary():String {
        val model=when {
            !health.modelConfigured -> "model none"
            health.modelLoaded -> "model loaded"
            else -> "model unavailable"
        }
        val relay=if(relayConfigured) "TURN ready" else "STUN/direct only"
        return "Transport OK · ${health.status} · $model · ICE $iceServerCount · $relay · WS ok"
    }
}

object TransportPreflight {
    private data class WebSocketProbeResult(
        val ok:Boolean,
        val credentialMode:String
    )

    private val client=OkHttpClient.Builder()
        .connectTimeout(15,TimeUnit.SECONDS)
        .callTimeout(45,TimeUnit.SECONDS)
        .build()

    fun run(baseUrl:String,profile:String,effect:String):TransportPreflightResult {
        require(baseUrl.isNotBlank()) { "Backend URL is empty" }
        val health=SessionApi.health(baseUrl)
        val ice=SessionApi.iceServers(baseUrl)
        val session=SessionApi.create(baseUrl,profile,effect)
        try {
            val validated=SessionApi.validate(baseUrl,session)
            check(validated) { "Backend did not validate the disposable session" }
            val websocket=probeWebSocket(baseUrl)
            check(websocket.ok) { "Live WebSocket probe was not acknowledged" }
            return TransportPreflightResult(
                health=health,
                iceServerCount=ice.size,
                relayConfigured=ice.any { server ->
                    server.urls.any { url ->
                        url.startsWith("turn:",ignoreCase=true) ||
                            url.startsWith("turns:",ignoreCase=true)
                    }
                },
                sessionValidated=validated,
                websocketOk=websocket.ok,
                credentialMode=websocket.credentialMode
            )
        } finally {
            runCatching { SessionApi.dispose(baseUrl,session) }
        }
    }

    internal fun liveWebSocketUrl(baseUrl:String):String {
        val normalized=baseUrl.trim().trimEnd('/')
        return when {
            normalized.startsWith("https://",ignoreCase=true) ->
                "wss://"+normalized.substringAfter("://")+"/v1/live"
            normalized.startsWith("http://",ignoreCase=true) ->
                "ws://"+normalized.substringAfter("://")+"/v1/live"
            normalized.startsWith("wss://",ignoreCase=true) ||
                normalized.startsWith("ws://",ignoreCase=true) ->
                normalized+"/v1/live"
            else -> throw IllegalArgumentException("Backend URL must use http:// or https://")
        }
    }

    private fun probeWebSocket(baseUrl:String):WebSocketProbeResult {
        val result=AtomicReference<WebSocketProbeResult?>()
        val error=AtomicReference<Throwable?>()
        val latch=CountDownLatch(1)
        val request=Request.Builder().url(liveWebSocketUrl(baseUrl)).build()

        val socket=client.newWebSocket(
            request,
            object:WebSocketListener() {
                override fun onOpen(webSocket:WebSocket,response:Response) {
                    webSocket.send("""{"action":"probe"}""")
                }

                override fun onMessage(webSocket:WebSocket,text:String) {
                    try {
                        val json=JSONObject(text)
                        if(json.optString("event")!="probe" || !json.optBoolean("ok",false)) {
                            error.compareAndSet(
                                null,
                                IllegalStateException(
                                    json.optString("detail","Unexpected WebSocket probe response")
                                )
                            )
                        } else {
                            result.compareAndSet(
                                null,
                                WebSocketProbeResult(
                                    ok=true,
                                    credentialMode=json.optString("credential_mode","unknown")
                                )
                            )
                        }
                    } catch(t:Throwable) {
                        error.compareAndSet(null,t)
                    } finally {
                        latch.countDown()
                        webSocket.close(1000,"probe complete")
                    }
                }

                override fun onFailure(webSocket:WebSocket,t:Throwable,response:Response?) {
                    error.compareAndSet(null,t)
                    latch.countDown()
                }
            }
        )

        if(!latch.await(45,TimeUnit.SECONDS)) {
            socket.cancel()
            throw IllegalStateException("Live WebSocket probe timed out")
        }
        error.get()?.let { throw it }
        return result.get() ?: throw IllegalStateException("Live WebSocket probe returned no result")
    }
}
