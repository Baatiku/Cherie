package com.baatiku.omnicam;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.util.Log;

import org.webrtc.VideoFrame;
import org.webrtc.VideoSink;

import java.nio.ByteBuffer;

public final class H264RelaySink implements VideoSink, AutoCloseable {
    private static final String TAG = "OmniCam";

    private final LocalH264Server server;
    private MediaCodec encoder;
    private int width;
    private int height;
    private int colorFormat;

    public H264RelaySink(LocalH264Server server) {
        this.server = server;
    }

    @Override
    public synchronized void onFrame(VideoFrame frame) {
        VideoFrame.I420Buffer i420 = frame.getBuffer().toI420();
        try {
            if (encoder == null) startEncoder(i420.getWidth(), i420.getHeight());

            int index = encoder.dequeueInputBuffer(0);
            if (index >= 0) {
                ByteBuffer in = encoder.getInputBuffer(index);
                if (in != null) {
                    in.clear();
                    packPlane(i420.getDataY(), i420.getStrideY(), width, height, in);
                    if (colorFormat == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar) {
                        packUvSemiPlanar(
                                i420.getDataU(),
                                i420.getStrideU(),
                                i420.getDataV(),
                                i420.getStrideV(),
                                width / 2,
                                height / 2,
                                in);
                    } else {
                        packPlane(i420.getDataU(), i420.getStrideU(), width / 2, height / 2, in);
                        packPlane(i420.getDataV(), i420.getStrideV(), width / 2, height / 2, in);
                    }
                    encoder.queueInputBuffer(
                            index,
                            0,
                            width * height * 3 / 2,
                            frame.getTimestampNs() / 1000L,
                            0);
                }
            }
            drain();
        } catch (Throwable t) {
            Log.e(TAG, "H264 encode failed", t);
        } finally {
            i420.release();
        }
    }

    private void startEncoder(int inputWidth, int inputHeight) throws Exception {
        width = inputWidth & ~1;
        height = inputHeight & ~1;

        encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
        MediaCodecInfo.CodecCapabilities caps = encoder.getCodecInfo()
                .getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC);
        colorFormat = chooseColorFormat(caps.colorFormats);

        MediaFormat f = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
        f.setInteger(MediaFormat.KEY_COLOR_FORMAT, colorFormat);
        f.setInteger(MediaFormat.KEY_BIT_RATE, 2_000_000);
        f.setInteger(MediaFormat.KEY_FRAME_RATE, 30);
        f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);

        encoder.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        encoder.start();

        server.setFormat(width, height);
        Log.i(TAG, "H264 relay encoder " + width + "x" + height);
    }

    private static void packPlane(
            ByteBuffer src, int stride, int w, int h, ByteBuffer dst) {
        ByteBuffer dup = src.duplicate();
        for (int row = 0; row < h; row++) {
            int start = row * stride;
            dup.position(start);
            dup.limit(start + w);
            dst.put(dup);
            dup.clear();
        }
    }

    private static int chooseColorFormat(int[] formats) {
        boolean flexible = false;
        for (int format : formats) {
            if (format == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar) {
                return format;
            }
            if (format == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar) {
                return format;
            }
            if (format == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible) {
                flexible = true;
            }
        }
        if (flexible) {
            return MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible;
        }
        throw new IllegalStateException("No supported YUV420 AVC encoder input format");
    }

    private static void packUvSemiPlanar(
            ByteBuffer u,
            int uStride,
            ByteBuffer v,
            int vStride,
            int w,
            int h,
            ByteBuffer dst) {
        ByteBuffer ub = u.duplicate();
        ByteBuffer vb = v.duplicate();
        for (int row = 0; row < h; row++) {
            for (int col = 0; col < w; col++) {
                dst.put(ub.get(row * uStride + col));
                dst.put(vb.get(row * vStride + col));
            }
        }
    }

    private void drain() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();

        while (true) {
            int outIndex = encoder.dequeueOutputBuffer(info, 0);
            if (outIndex == MediaCodec.INFO_TRY_AGAIN_LATER) return;

            if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                MediaFormat fmt = encoder.getOutputFormat();
                ByteBuffer c0 = fmt.getByteBuffer("csd-0");
                ByteBuffer c1 = fmt.getByteBuffer("csd-1");
                if (c0 != null) {
                    int n0 = c0.remaining();
                    int n1 = c1 == null ? 0 : c1.remaining();
                    byte[] config = new byte[n0 + n1];
                    c0.get(config, 0, n0);
                    if (c1 != null) c1.get(config, n0, n1);
                    server.publish(LocalH264Server.TYPE_CONFIG, 0, config);
                }
                continue;
            }

            if (outIndex < 0) return;

            ByteBuffer out = encoder.getOutputBuffer(outIndex);
            if (out != null && info.size > 0) {
                out.position(info.offset);
                out.limit(info.offset + info.size);
                byte[] data = new byte[info.size];
                out.get(data);

                byte type = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0
                        ? LocalH264Server.TYPE_CONFIG
                        : LocalH264Server.TYPE_FRAME;
                server.publish(type, info.presentationTimeUs, data);
            }
            encoder.releaseOutputBuffer(outIndex, false);
        }
    }

    @Override
    public synchronized void close() {
        if (encoder != null) {
            try { encoder.stop(); } catch (Throwable ignored) {}
            try { encoder.release(); } catch (Throwable ignored) {}
            encoder = null;
        }
    }
}
