package com.baatiku.omnicam;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;
import org.webrtc.IceCandidate;
import org.webrtc.MediaConstraints;
import org.webrtc.MediaStream;
import org.webrtc.PeerConnection;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.RtpReceiver;
import org.webrtc.SdpObserver;
import org.webrtc.SessionDescription;
import org.webrtc.VideoTrack;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public final class RtcClient implements AutoCloseable {
    private static final String TAG = "OmniCam";
    private static final MediaType JSON = MediaType.get("application/json");

    private final PeerConnectionFactory factory;
    private final String endpoint;
    private final String token;
    private final String identityB64;
    private final VideoTrack localTrack;
    private final Consumer<VideoTrack> remoteVideo;
    private final Consumer<String> status;
    private final OkHttpClient http = new OkHttpClient();
    private final AtomicBoolean offerPosted = new AtomicBoolean();

    private PeerConnection pc;

    public RtcClient(
            PeerConnectionFactory factory,
            String endpoint,
            String token,
            String identityB64,
            VideoTrack localTrack,
            Consumer<VideoTrack> remoteVideo,
            Consumer<String> status) {
        this.factory = factory;
        this.endpoint = endpoint.replaceAll("/+$", "");
        this.token = token;
        this.identityB64 = identityB64;
        this.localTrack = localTrack;
        this.remoteVideo = remoteVideo;
        this.status = status;
    }

    public void start() {
        new Thread(() -> {
            try {
                List<PeerConnection.IceServer> ice = fetchIceServers();
                PeerConnection.RTCConfiguration cfg =
                        new PeerConnection.RTCConfiguration(ice);
                cfg.sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN;

                pc = factory.createPeerConnection(cfg, observer());
                if (pc == null) {
                    throw new IllegalStateException("PeerConnection creation failed");
                }

                pc.addTrack(localTrack, Collections.singletonList("omnicam"));
                pc.createOffer(new SdpAdapter() {
                    @Override
                    public void onCreateSuccess(SessionDescription sdp) {
                        pc.setLocalDescription(new SdpAdapter() {
                            @Override
                            public void onSetSuccess() {
                                status.accept("Local SDP created; gathering ICE");
                                Thread timeout = new Thread(() -> {
                                    try { Thread.sleep(4500); }
                                    catch (InterruptedException ignored) {
                                        Thread.currentThread().interrupt();
                                    }
                                    postOfferIfNeeded();
                                }, "omnicam-ice-timeout");
                                timeout.setDaemon(true);
                                timeout.start();
                            }
                        }, sdp);
                    }
                }, new MediaConstraints());
            } catch (Throwable t) {
                Log.e(TAG, "RTC start failed", t);
                status.accept("RTC start failed: " + t.getMessage());
            }
        }, "omnicam-rtc").start();
    }

    private List<PeerConnection.IceServer> fetchIceServers() throws Exception {
        Request request = new Request.Builder()
                .url(endpoint + "/ice-servers")
                .header("Authorization", "Bearer " + token)
                .build();

        try (Response response = http.newCall(request).execute()) {
            if (!response.isSuccessful() || response.body() == null) {
                throw new IOException("ICE endpoint HTTP " + response.code());
            }

            JSONObject root = new JSONObject(response.body().string());
            JSONArray arr = root.getJSONArray("ice_servers");
            List<PeerConnection.IceServer> out = new ArrayList<>();

            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.getJSONObject(i);
                JSONArray urls = item.getJSONArray("urls");
                List<String> u = new ArrayList<>();
                for (int j = 0; j < urls.length(); j++) u.add(urls.getString(j));

                PeerConnection.IceServer.Builder b = PeerConnection.IceServer.builder(u);
                if (item.has("username")) {
                    b.setUsername(item.optString("username", ""));
                }
                if (item.has("credential")) {
                    b.setPassword(item.optString("credential", ""));
                }
                out.add(b.createIceServer());
            }
            return out;
        }
    }

    private PeerConnection.Observer observer() {
        return new PeerConnection.Observer() {
            @Override public void onSignalingChange(PeerConnection.SignalingState s) {}

            @Override
            public void onIceConnectionChange(PeerConnection.IceConnectionState s) {
                status.accept("ICE: " + s);
            }

            @Override public void onIceConnectionReceivingChange(boolean b) {}

            @Override
            public void onIceGatheringChange(PeerConnection.IceGatheringState s) {
                if (s == PeerConnection.IceGatheringState.COMPLETE) {
                    postOfferIfNeeded();
                }
            }

            @Override public void onIceCandidate(IceCandidate c) {}
            @Override public void onIceCandidatesRemoved(IceCandidate[] c) {}
            @Override public void onAddStream(MediaStream s) {}
            @Override public void onRemoveStream(MediaStream s) {}
            @Override public void onDataChannel(org.webrtc.DataChannel d) {}
            @Override public void onRenegotiationNeeded() {}

            @Override
            public void onAddTrack(RtpReceiver receiver, MediaStream[] streams) {
                if (receiver.track() instanceof VideoTrack) {
                    remoteVideo.accept((VideoTrack) receiver.track());
                }
            }
        };
    }

    private void postOfferIfNeeded() {
        PeerConnection connection = pc;
        if (connection == null ||
                connection.getLocalDescription() == null ||
                !offerPosted.compareAndSet(false, true)) {
            return;
        }

        try {
            JSONObject body = new JSONObject();
            body.put("type", connection.getLocalDescription().type.canonicalForm());
            body.put("sdp", connection.getLocalDescription().description);
            body.put("identity_b64", identityB64);

            Request request = new Request.Builder()
                    .url(endpoint + "/offer")
                    .header("Authorization", "Bearer " + token)
                    .post(RequestBody.create(body.toString(), JSON))
                    .build();

            http.newCall(request).enqueue(new Callback() {
                @Override
                public void onFailure(Call call, IOException e) {
                    status.accept("Offer failed: " + e.getMessage());
                }

                @Override
                public void onResponse(Call call, Response response) throws IOException {
                    try (response) {
                        if (!response.isSuccessful() || response.body() == null) {
                            status.accept("Offer HTTP " + response.code());
                            return;
                        }

                        JSONObject answer = new JSONObject(response.body().string());
                        SessionDescription remote = new SessionDescription(
                                SessionDescription.Type.fromCanonicalForm(
                                        answer.getString("type")),
                                answer.getString("sdp"));

                        connection.setRemoteDescription(new SdpAdapter() {
                            @Override
                            public void onSetSuccess() {
                                status.accept("WebRTC answer applied");
                            }
                        }, remote);
                    } catch (Throwable t) {
                        status.accept("Answer parse failed: " + t.getMessage());
                    }
                }
            });
        } catch (Throwable t) {
            status.accept("Offer build failed: " + t.getMessage());
        }
    }

    @Override
    public void close() {
        try { if (pc != null) pc.close(); } catch (Throwable ignored) {}
        pc = null;
        http.dispatcher().cancelAll();
        http.connectionPool().evictAll();
    }

    private static class SdpAdapter implements SdpObserver {
        @Override public void onCreateSuccess(SessionDescription s) {}
        @Override public void onSetSuccess() {}
        @Override public void onCreateFailure(String e) {
            Log.e(TAG, "SDP create failed: " + e);
        }
        @Override public void onSetFailure(String e) {
            Log.e(TAG, "SDP set failed: " + e);
        }
    }
}
