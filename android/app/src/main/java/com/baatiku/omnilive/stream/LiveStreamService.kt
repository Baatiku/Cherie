package com.baatiku.omnilive.stream

import android.app.*
import android.content.Intent
import android.os.IBinder

class LiveStreamService:Service() {
    companion object { const val CHANNEL_ID="omnilive_stream"; const val NOTIFICATION_ID=1001 }
    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CHANNEL_ID,"OmniLive session",NotificationManager.IMPORTANCE_LOW))
    }
    override fun onStartCommand(intent:Intent?, flags:Int, startId:Int):Int {
        val profile = QualityProfile.fromName(intent?.getStringExtra("profile") ?: "balanced")
        val text = profile.width.toString()+"×"+profile.height+" @ "+profile.cameraFps+" fps"
        val n = Notification.Builder(this,CHANNEL_ID).setContentTitle("OmniLive active").setContentText(text).setSmallIcon(android.R.drawable.presence_video_online).build()
        startForeground(NOTIFICATION_ID,n)
        return START_NOT_STICKY
    }
    override fun onBind(intent:Intent?):IBinder? = null
}
