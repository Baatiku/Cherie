package com.baatiku.omnicam;

import android.util.Log;

import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataOutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

public final class LocalH264Server implements Closeable {
    public static final byte TYPE_CONFIG = 0;
    public static final byte TYPE_FRAME = 1;

    private static final String TAG = "OmniCam";

    private final int port;
    private final List<DataOutputStream> clients = new CopyOnWriteArrayList<>();
    private volatile boolean running;
    private volatile int width;
    private volatile int height;
    private volatile byte[] codecConfig;
    private ServerSocket server;

    public LocalH264Server(int port) {
        this.port = port;
    }

    public void start() throws Exception {
        server = new ServerSocket(port, 4, InetAddress.getByName("127.0.0.1"));
        running = true;
        Thread t = new Thread(this::acceptLoop, "omnicam-relay");
        t.setDaemon(true);
        t.start();
    }

    public synchronized void setFormat(int width, int height) {
        this.width = width;
        this.height = height;
        notifyAll();
    }

    private synchronized void awaitFormat() throws InterruptedException {
        while (running && (width <= 0 || height <= 0)) wait(250);
    }

    private void acceptLoop() {
        while (running) {
            try {
                Socket socket = server.accept();
                socket.setTcpNoDelay(true);
                awaitFormat();
                if (!running) {
                    socket.close();
                    break;
                }

                DataOutputStream out = new DataOutputStream(
                        new BufferedOutputStream(socket.getOutputStream(), 128 * 1024));
                out.write("OMNI1".getBytes(StandardCharsets.US_ASCII));
                out.writeInt(width);
                out.writeInt(height);
                out.flush();
                clients.add(out);

                byte[] config = codecConfig;
                if (config != null) writePacket(out, TYPE_CONFIG, 0, config);
                Log.i(TAG, "Virtual-camera client connected; clients=" + clients.size());
            } catch (Throwable t) {
                if (running) Log.w(TAG, "Relay accept failed", t);
            }
        }
    }

    public void publish(byte type, long ptsUs, byte[] payload) {
        if (type == TYPE_CONFIG) codecConfig = payload.clone();

        for (DataOutputStream out : clients) {
            try {
                writePacket(out, type, ptsUs, payload);
            } catch (Throwable t) {
                clients.remove(out);
                try { out.close(); } catch (Throwable ignored) {}
            }
        }
    }

    private static void writePacket(
            DataOutputStream out, byte type, long ptsUs, byte[] payload) throws Exception {
        synchronized (out) {
            out.writeByte(type);
            out.writeLong(ptsUs);
            out.writeInt(payload.length);
            out.write(payload);
            out.flush();
        }
    }

    @Override
    public void close() {
        running = false;
        synchronized (this) { notifyAll(); }
        try { if (server != null) server.close(); } catch (Throwable ignored) {}
        for (DataOutputStream out : clients) {
            try { out.close(); } catch (Throwable ignored) {}
        }
        clients.clear();
    }
}
