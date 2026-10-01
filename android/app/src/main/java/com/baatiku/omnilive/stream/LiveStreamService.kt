package com.baatiku.omnilive.stream

import android.app.*
import android.content.Intent
import android.os.IBinder
import com.baatiku.omnilive.BuildConfig

class LiveStreamService:Service() {
    companion object { const val CHANNEL_ID="omnilive_stream"; const val NOTIFICATION_ID=1001 }
    private var client:WebRtcClient?=null
    private var deviceHealth:DeviceHealthMonitor?=null

    override fun onCreate() {
        super.onCreate()
        deviceHealth=DeviceHealthMonitor(this).also { it.start() }
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID,"OmniLive session",NotificationManager.IMPORTANCE_LOW)
        )
    }

    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        val profileName=intent?.getStringExtra("profile") ?: "balanced"
        val effectName=intent?.getStringExtra("effect") ?: "custom_onnx"
        val backendUrl=intent?.getStringExtra("backend_url") ?: BuildConfig.DEFAULT_BACKEND_URL
        val referenceImagePath=intent?.getStringExtra("reference_image_path")
        val profile=QualityProfile.fromName(profileName)
        LiveSessionState.starting(profileName,effectName,backendUrl)

        return try {
            val text=profile.width.toString()+"×"+profile.height+" @ "+profile.cameraFps+" fps"
            val n=Notification.Builder(this,CHANNEL_ID)
                .setContentTitle("OmniLive active").setContentText(text)
                .setSmallIcon(android.R.drawable.presence_video_online).build()
            startForeground(NOTIFICATION_ID,n)

            if(client==null) {
                client=WebRtcClient(this,backendUrl,profileName,effectName,referenceImagePath)
                client!!.start()
            }
            LiveSessionState.active()
            START_NOT_STICKY
        } catch(t:Throwable) {
            LiveSessionState.failed(t)
            client?.stop()
            client=null
            stopSelf(startId)
            START_NOT_STICKY
        }
    }

    override fun onDestroy() {
        LiveSessionState.stopping()
        client?.stop()
        client=null
        deviceHealth?.stop()
        deviceHealth=null
        LiveSessionState.idle()
        super.onDestroy()
    }
    override fun onBind(intent:Intent?):IBinder?=null
}
