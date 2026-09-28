package com.baatiku.omnicam;

import android.net.Credentials;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.util.Log;

import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

public final class LocalH264Server implements Closeable {
    public static final byte TYPE_CONFIG = 0;
    public static final byte TYPE_FRAME = 1;

    private static final String TAG = "OmniCam";

    private final String socketName;
    private final int allowedUid;
    private final List<Client> clients = new CopyOnWriteArrayList<>();

    private volatile boolean running;
    private volatile int width;
    private volatile int height;
    private volatile byte[] codecConfig;
    private LocalServerSocket server;

    public LocalH264Server(String socketName, int allowedUid) {
        this.socketName = socketName;
        this.allowedUid = allowedUid;
    }

    public void start() throws Exception {
        server = new LocalServerSocket(socketName);
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
        while (running && (width <= 0 || height <= 0)) {
            wait(250);
        }
    }

    private void acceptLoop() {
        while (running) {
            LocalSocket socket = null;
            try {
                socket = server.accept();
                Credentials peer = socket.getPeerCredentials();

                if (peer == null || peer.getUid() != allowedUid) {
                    Log.w(
                            TAG,
                            "Rejected virtual-camera client uid="
                                    + (peer == null ? -1 : peer.getUid()));
                    socket.close();
                    continue;
                }

                awaitFormat();
                if (!running) {
                    socket.close();
                    break;
                }

                socket.setSendBufferSize(256 * 1024);
                DataOutputStream out = new DataOutputStream(
                        new BufferedOutputStream(
                                socket.getOutputStream(),
                                128 * 1024));

                out.write("OMNI1".getBytes(StandardCharsets.US_ASCII));
                out.writeInt(width);
                out.writeInt(height);
                out.flush();

                Client client = new Client(socket, out);
                clients.add(client);

                byte[] config = codecConfig;
                if (config != null) {
                    writePacket(client, TYPE_CONFIG, 0, config);
                }

                Log.i(
                        TAG,
                        "Authorized Zoom virtual-camera client connected; clients="
                                + clients.size());
            } catch (Throwable t) {
                if (socket != null) {
                    try { socket.close(); } catch (Throwable ignored) {}
                }
                if (running) {
                    Log.w(TAG, "Relay accept failed", t);
                }
            }
        }
    }

    public void publish(byte type, long ptsUs, byte[] payload) {
        if (type == TYPE_CONFIG) {
            codecConfig = payload.clone();
        }

        for (Client client : clients) {
            try {
                writePacket(client, type, ptsUs, payload);
            } catch (Throwable t) {
                clients.remove(client);
                client.close();
            }
        }
    }

    private static void writePacket(
            Client client,
            byte type,
            long ptsUs,
            byte[] payload) throws Exception {
        synchronized (client.out) {
            client.out.writeByte(type);
            client.out.writeLong(ptsUs);
            client.out.writeInt(payload.length);
            client.out.write(payload);
            client.out.flush();
        }
    }

    @Override
    public void close() {
        running = false;
        synchronized (this) {
            notifyAll();
        }

        try {
            if (server != null) server.close();
        } catch (Throwable ignored) {}

        for (Client client : clients) {
            client.close();
        }
        clients.clear();
    }

    private static final class Client {
        final LocalSocket socket;
        final DataOutputStream out;

        Client(LocalSocket socket, DataOutputStream out) {
            this.socket = socket;
            this.out = out;
        }

        void close() {
            try { out.close(); } catch (Throwable ignored) {}
            try { socket.close(); } catch (Throwable ignored) {}
        }
    }
}
