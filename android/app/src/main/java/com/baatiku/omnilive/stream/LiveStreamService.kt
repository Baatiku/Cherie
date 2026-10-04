package com.baatiku.omnilive.stream

import android.app.*
import android.content.Intent
import android.os.IBinder
import com.baatiku.omnilive.BuildConfig
import com.baatiku.omnilive.diagnostics.ServiceLog

class LiveStreamService:Service() {
    companion object { const val CHANNEL_ID="omnilive_stream"; const val NOTIFICATION_ID=1001 }
    private var client:WebRtcClient?=null
    private var deviceHealth:DeviceHealthMonitor?=null

    override fun onCreate() {
        super.onCreate()
        ServiceLog.installCrashHandler(this)
        ServiceLog.append(this,"service","onCreate")
        deviceHealth=DeviceHealthMonitor(this).also { it.start() }
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID,"OmniLive session",NotificationManager.IMPORTANCE_LOW)
        )
    }

    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        val profileName=intent?.getStringExtra("profile") ?: "balanced"
        val effectName=intent?.getStringExtra("effect") ?: "face_swap"
        val backendUrl=intent?.getStringExtra("backend_url") ?: BuildConfig.DEFAULT_BACKEND_URL
        val referenceImagePath=intent?.getStringExtra("reference_image_path")
        val profile=QualityProfile.fromName(profileName)
        ServiceLog.append(this,"service","onStart profile=$profileName effect=$effectName backend=$backendUrl ref=${referenceImagePath!=null}")
        LiveSessionState.starting(profileName,effectName,backendUrl)

        return try {
            val text=profile.width.toString()+"×"+profile.height+" @ "+profile.cameraFps+" fps"
            val n=Notification.Builder(this,CHANNEL_ID)
                .setContentTitle("OmniLive active").setContentText(text)
                .setSmallIcon(android.R.drawable.presence_video_online).build()
            startForeground(NOTIFICATION_ID,n)
            ServiceLog.append(this,"service","foreground started")

            if(client==null) {
                client=WebRtcClient(this,backendUrl,profileName,effectName,referenceImagePath)
                ServiceLog.append(this,"service","client created")
                client!!.start()
                ServiceLog.append(this,"service","client started")
            }
            LiveSessionState.active()
            START_NOT_STICKY
        } catch(t:Throwable) {
            ServiceLog.append(this,"service","start FAILED: ${t.javaClass.simpleName}: ${t.message}")
            LiveSessionState.failed(t)
            client?.stop()
            client=null
            stopSelf(startId)
            START_NOT_STICKY
        }
    }

    override fun onDestroy() {
        ServiceLog.append(this,"service","onDestroy")
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
