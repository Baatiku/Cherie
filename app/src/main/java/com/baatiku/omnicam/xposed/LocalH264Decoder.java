package com.baatiku.omnicam.xposed;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.util.Log;
import android.view.Surface;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

final class LocalH264Decoder implements AutoCloseable {
    private static final String TAG = "OmniCam";

    private final Surface output;
    private volatile boolean running = true;
    private volatile MediaCodec decoder;
    private volatile Socket socket;

    LocalH264Decoder(Surface output) {
        this.output = output;
    }

    void start() {
        Thread t = new Thread(this::run, "omnicam-zoom-decoder");
        t.setDaemon(true);
        t.start();
    }

    private void run() {
        int backoff = 100;

        while (running) {
            try {
                socket = new Socket();
                socket.connect(
                        new InetSocketAddress("127.0.0.1", 7878),
                        1000);
                socket.setTcpNoDelay(true);

                DataInputStream in = new DataInputStream(
                        new BufferedInputStream(
                                socket.getInputStream(),
                                128 * 1024));

                byte[] magic = new byte[5];
                in.readFully(magic);
                if (!Arrays.equals(
                        magic,
                        "OMNI1".getBytes(StandardCharsets.US_ASCII))) {
                    throw new IllegalStateException("Bad relay magic");
                }

                int width = in.readInt();
                int height = in.readInt();
                startDecoder(width, height);
                backoff = 100;

                while (running) {
                    byte type = in.readByte();
                    long ptsUs = in.readLong();
                    int len = in.readInt();
                    if (len <= 0 || len > 2 * 1024 * 1024) {
                        throw new IllegalStateException(
                                "Bad H264 packet length: " + len);
                    }
                    byte[] data = new byte[len];
                    in.readFully(data);
                    feed(
                            data,
                            ptsUs,
                            type == 0);
                }
            } catch (Throwable t) {
                if (running) {
                    Log.w(TAG, "Virtual stream unavailable; retrying", t);
                }
            } finally {
                stopDecoder();
                try {
                    if (socket != null) socket.close();
                } catch (Throwable ignored) {}
            }

            if (running) {
                try {
                    Thread.sleep(backoff);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
                backoff = Math.min(backoff * 2, 2000);
            }
        }
    }

    private void startDecoder(int width, int height) throws Exception {
        MediaFormat f = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC,
                width,
                height);

        decoder = MediaCodec.createDecoderByType(
                MediaFormat.MIMETYPE_VIDEO_AVC);
        decoder.configure(f, output, null, 0);
        decoder.start();
        Log.i(TAG, "Zoom virtual decoder " + width + "x" + height);
    }

    private void feed(
            byte[] data,
            long ptsUs,
            boolean codecConfig) throws Exception {
        MediaCodec d = decoder;
        if (d == null) return;

        int idx = d.dequeueInputBuffer(10_000);
        if (idx >= 0) {
            ByteBuffer b = d.getInputBuffer(idx);
            if (b != null) {
                b.clear();
                b.put(data);
                d.queueInputBuffer(
                        idx,
                        0,
                        data.length,
                        ptsUs,
                        codecConfig
                                ? MediaCodec.BUFFER_FLAG_CODEC_CONFIG
                                : 0);
            }
        }

        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            int out = d.dequeueOutputBuffer(info, 0);
            if (out < 0) break;
            d.releaseOutputBuffer(out, true);
        }
    }

    private void stopDecoder() {
        MediaCodec d = decoder;
        decoder = null;

        if (d != null) {
            try { d.stop(); } catch (Throwable ignored) {}
            try { d.release(); } catch (Throwable ignored) {}
        }
    }

    @Override
    public void close() {
        running = false;
        try {
            if (socket != null) socket.close();
        } catch (Throwable ignored) {}
        stopDecoder();
    }
}
