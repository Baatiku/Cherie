package com.baatiku.omnicam;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Base64;
import android.util.Log;

import org.webrtc.Camera1Enumerator;
import org.webrtc.Camera2Enumerator;
import org.webrtc.CameraEnumerator;
import org.webrtc.CameraVideoCapturer;
import org.webrtc.EglBase;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.SurfaceTextureHelper;
import org.webrtc.VideoCapturer;
import org.webrtc.VideoSource;
import org.webrtc.VideoTrack;

import java.io.File;
import java.nio.file.Files;

public final class StreamingService extends Service {
    private static final String TAG = "OmniCam";
    private static final String CHANNEL = "omnicam_live";

    private EglBase egl;
    private SurfaceTextureHelper textureHelper;
    private VideoCapturer capturer;
    private VideoSource source;
    private VideoTrack localTrack;
    private RtcClient rtc;
    private LocalH264Server relay;
    private H264RelaySink sink;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(
                CHANNEL, "OmniCam Live", NotificationManager.IMPORTANCE_LOW));

        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle("OmniCam Live")
                .setContentText("Live virtual camera is active")
                .setSmallIcon(android.R.drawable.presence_video_online)
                .setOngoing(true)
                .build();
        startForeground(101, n);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String endpoint = intent == null ? null : intent.getStringExtra("endpoint");
        String token = intent == null ? null : intent.getStringExtra("token");
        if (endpoint == null || token == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        new Thread(() -> startPipeline(endpoint, token), "omnicam-start").start();
        return START_STICKY;
    }

    private synchronized void startPipeline(String endpoint, String token) {
        if (rtc != null) return;
        try {
            byte[] identity = Files.readAllBytes(new File(getFilesDir(), "identity.jpg").toPath());

            int zoomUid = getPackageManager()
                    .getApplicationInfo("us.zoom.videomeetings", 0)
                    .uid;
            relay = new LocalH264Server("omnicam-live", zoomUid);
            relay.start();

            PeerConnectionFactory.initialize(
                    PeerConnectionFactory.InitializationOptions.builder(this)
                            .setEnableInternalTracer(false)
                            .createInitializationOptions());

            egl = EglBase.create();
            PeerConnectionFactory factory = PeerConnectionFactory.builder()
                    .createPeerConnectionFactory();

            capturer = createFrontCapturer();
            if (capturer == null) throw new IllegalStateException("No usable front camera");

            textureHelper = SurfaceTextureHelper.create(
                    "OmniCamCapture", egl.getEglBaseContext());
            source = factory.createVideoSource(false);
            capturer.initialize(textureHelper, this, source.getCapturerObserver());
            capturer.startCapture(640, 480, 30);

            localTrack = factory.createVideoTrack("omnicam-camera", source);
            sink = new H264RelaySink(relay);

            rtc = new RtcClient(
                    factory,
                    endpoint,
                    token,
                    Base64.encodeToString(identity, Base64.NO_WRAP),
                    localTrack,
                    remoteTrack -> {
                        remoteTrack.addSink(sink);
                        Log.i(TAG, "Remote processed video connected; local H264 relay armed");
                    },
                    message -> Log.i(TAG, message));
            rtc.start();
        } catch (Throwable t) {
            Log.e(TAG, "Pipeline start failed", t);
            stopSelf();
        }
    }

    private VideoCapturer createFrontCapturer() {
        CameraEnumerator enumerator = Camera2Enumerator.isSupported(this)
                ? new Camera2Enumerator(this)
                : new Camera1Enumerator(false);

        for (String name : enumerator.getDeviceNames()) {
            if (!enumerator.isFrontFacing(name)) continue;
            CameraVideoCapturer c = enumerator.createCapturer(name, null);
            if (c != null) return c;
        }
        return null;
    }

    @Override
    public synchronized void onDestroy() {
        try { if (rtc != null) rtc.close(); } catch (Throwable ignored) {}
        try { if (capturer != null) capturer.stopCapture(); } catch (Throwable ignored) {}
        try { if (capturer != null) capturer.dispose(); } catch (Throwable ignored) {}
        try { if (source != null) source.dispose(); } catch (Throwable ignored) {}
        try { if (textureHelper != null) textureHelper.dispose(); } catch (Throwable ignored) {}
        try { if (sink != null) sink.close(); } catch (Throwable ignored) {}
        try { if (relay != null) relay.close(); } catch (Throwable ignored) {}
        try { if (egl != null) egl.release(); } catch (Throwable ignored) {}
        rtc = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
