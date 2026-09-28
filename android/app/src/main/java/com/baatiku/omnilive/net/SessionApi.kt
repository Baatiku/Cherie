package com.baatiku.omnilive.net

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class LiveSession(val sessionId:String,val token:String,val expiresAt:Long)
data class IceServerConfig(val urls:List<String>,val username:String,val credential:String)
data class BackendTelemetry(val processorP95Ms:Long)

object SessionApi {
    private fun connection(url:String,method:String):HttpURLConnection {
        return (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod=method
            connectTimeout=8000
            readTimeout=20000
            setRequestProperty("Content-Type","application/json")
        }
    }

    fun create(baseUrl:String,profile:String,effect:String):LiveSession {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/sessions","POST")
        conn.doOutput=true
        val body=JSONObject().put("profile",profile).put("effect",effect)
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        check(conn.responseCode in 200..299) { "Session creation failed: "+conn.responseCode }
        val json=JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        return LiveSession(json.getString("session_id"),json.getString("token"),json.getLong("expires_at"))
    }

    fun iceServers(baseUrl:String):List<IceServerConfig> {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/ice","GET")
        check(conn.responseCode in 200..299) { "ICE config failed: "+conn.responseCode }
        val array=JSONObject(conn.inputStream.bufferedReader().use { it.readText() }).getJSONArray("servers")
        return (0 until array.length()).map { index ->
            val item=array.getJSONObject(index)
            val urlsArray=item.getJSONArray("urls")
            val urls=(0 until urlsArray.length()).map { urlsArray.getString(it) }
            IceServerConfig(urls,item.optString("username"),item.optString("credential"))
        }
    }

    fun telemetry(baseUrl:String):BackendTelemetry {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/telemetry","GET")
        check(conn.responseCode in 200..299) { "Telemetry failed: "+conn.responseCode }
        val json=JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        return BackendTelemetry(json.optDouble("processor_p95_ms",0.0).toLong())
    }

    fun exchangeOffer(baseUrl:String,session:LiveSession,sdp:String):Pair<String,String> {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/webrtc/offer","POST")
        conn.doOutput=true
        val body=JSONObject().put("session_id",session.sessionId).put("token",session.token).put("type","offer").put("sdp",sdp)
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        check(conn.responseCode in 200..299) { "Offer exchange failed: "+conn.responseCode }
        val json=JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        return json.getString("type") to json.getString("sdp")
    }
}
