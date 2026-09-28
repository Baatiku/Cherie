package com.baatiku.omnilive.net

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class LiveSession(val sessionId:String,val token:String,val expiresAt:Long)

object SessionApi {
    fun create(baseUrl:String, profile:String):LiveSession {
        val conn = URL(baseUrl.trimEnd('/') + "/v1/sessions").openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        conn.doOutput = true
        conn.setRequestProperty("Content-Type","application/json")
        conn.outputStream.use { it.write(JSONObject().put("profile",profile).toString().toByteArray()) }
        check(conn.responseCode in 200..299) { "Session creation failed: " + conn.responseCode }
        val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        return LiveSession(json.getString("session_id"),json.getString("token"),json.getLong("expires_at"))
    }

    fun exchangeOffer(baseUrl:String, session:LiveSession, sdp:String):Pair<String,String> {
        val conn = URL(baseUrl.trimEnd('/') + "/v1/webrtc/offer").openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.connectTimeout = 15000
        conn.readTimeout = 20000
        conn.doOutput = true
        conn.setRequestProperty("Content-Type","application/json")
        val body = JSONObject().put("session_id",session.sessionId).put("token",session.token).put("type","offer").put("sdp",sdp)
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        check(conn.responseCode in 200..299) { "Offer exchange failed: " + conn.responseCode }
        val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        return json.getString("type") to json.getString("sdp")
    }
}
