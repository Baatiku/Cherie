package com.baatiku.omnilive.net

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class LiveSession(val sessionId:String,val token:String,val expiresAt:Long)
data class IceServerConfig(val urls:List<String>,val username:String,val credential:String)
data class BackendTelemetry(
    val processorP95Ms:Long,
    val detectorP95Ms:Long,
    val modelP95Ms:Long,
    val targetNeuralFps:Double,
    val droppedInferenceFrames:Long,
    val inputOverwrittenFrames:Long
)

data class BackendHealth(
    val ok:Boolean,
    val status:String,
    val service:String,
    val modelConfigured:Boolean,
    val modelLoaded:Boolean,
    val providers:List<String>
)

object SessionApi {
    private fun connection(url:String,method:String):HttpURLConnection {
        return (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod=method
            connectTimeout=10000
            readTimeout=25000
            setRequestProperty("Content-Type","application/json")
        }
    }

    fun create(baseUrl:String,profile:String,effect:String):LiveSession {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/sessions","POST")
        try {
            conn.doOutput=true
            val body=JSONObject().put("profile",profile).put("effect",effect)
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            check(conn.responseCode in 200..299) { "Session creation failed: "+conn.responseCode }
            val json=JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            return LiveSession(json.getString("session_id"),json.getString("token"),json.getLong("expires_at"))
        } finally {
            conn.disconnect()
        }
    }

    fun iceServers(baseUrl:String):List<IceServerConfig> {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/ice","GET")
        try {
            check(conn.responseCode in 200..299) { "ICE config failed: "+conn.responseCode }
            val array=JSONObject(conn.inputStream.bufferedReader().use { it.readText() }).getJSONArray("servers")
            return (0 until array.length()).map { index ->
                val item=array.getJSONObject(index)
                val urlsArray=item.getJSONArray("urls")
                val urls=(0 until urlsArray.length()).map { urlsArray.getString(it) }
                IceServerConfig(urls,item.optString("username"),item.optString("credential"))
            }
        } finally {
            conn.disconnect()
        }
    }

    fun parseTelemetry(json:JSONObject):BackendTelemetry {
        return BackendTelemetry(
            processorP95Ms=json.optDouble("processor_p95_ms",0.0).toLong(),
            detectorP95Ms=json.optDouble("detector_p95_ms",0.0).toLong(),
            modelP95Ms=json.optDouble("model_p95_ms",0.0).toLong(),
            targetNeuralFps=json.optDouble("target_neural_fps",0.0),
            droppedInferenceFrames=json.optLong("dropped_inference_frames",0L),
            inputOverwrittenFrames=json.optLong("input_overwritten_frames",0L)
        )
    }

    fun parseHealth(json:JSONObject):BackendHealth {
        val providersArray=json.optJSONArray("providers")
        val providers=if(providersArray==null) emptyList() else
            (0 until providersArray.length()).map { providersArray.optString(it) }
                .filter { it.isNotBlank() }
        return BackendHealth(
            ok=json.optBoolean("ok",false),
            status=json.optString("status","unknown"),
            service=json.optString("service","unknown"),
            modelConfigured=json.optBoolean("configured",false),
            modelLoaded=json.optBoolean("loaded",false),
            providers=providers
        )
    }

    fun health(baseUrl:String):BackendHealth {
        require(baseUrl.isNotBlank()) { "Backend URL is empty" }
        val conn=connection(baseUrl.trimEnd('/')+"/health","GET")
        try {
            val code=conn.responseCode
            check(code in 200..299) { "Health check failed: $code" }
            val json=JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            return parseHealth(json)
        } finally {
            conn.disconnect()
        }
    }

    fun validate(baseUrl:String,session:LiveSession):Boolean {
        val conn=connection(
            baseUrl.trimEnd('/')+"/v1/sessions/"+session.sessionId+"/validate",
            "GET"
        )
        conn.setRequestProperty("Authorization","Bearer "+session.token)
        try {
            val code=conn.responseCode
            check(code in 200..299) { "Session validation failed: $code" }
            return JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optBoolean("ok",false)
        } finally {
            conn.disconnect()
        }
    }

    fun dispose(baseUrl:String,session:LiveSession):Boolean {
        val conn=connection(
            baseUrl.trimEnd('/')+"/v1/sessions/"+session.sessionId,
            "DELETE"
        )
        conn.setRequestProperty("Authorization","Bearer "+session.token)
        try {
            val code=conn.responseCode
            check(code in 200..299) { "Session disposal failed: $code" }
            return JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optBoolean("ok",false)
        } finally {
            conn.disconnect()
        }
    }

    fun telemetry(baseUrl:String):BackendTelemetry {
        val conn=connection(baseUrl.trimEnd('/')+"/v1/telemetry","GET")
        check(conn.responseCode in 200..299) { "Telemetry failed: "+conn.responseCode }
        return parseTelemetry(JSONObject(conn.inputStream.bufferedReader().use { it.readText() }))
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
