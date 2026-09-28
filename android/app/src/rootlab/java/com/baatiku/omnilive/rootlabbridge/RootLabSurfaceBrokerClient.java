package com.baatiku.omnilive.rootlabbridge;

import android.app.Application;
import android.content.*;
import android.os.*;
import android.util.Log;
import android.view.Surface;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/**
 * Zoom-process half of the rootlab surface broker.
 *
 * The class is loaded only by the rootlab Xposed module. It binds explicitly to OmniLive and sends
 * framework Surface parcelables over Messenger. No OmniLive transport/model code enters Zoom.
 */
final class RootLabSurfaceBrokerClient {
    private static final String TAG = "OmniLiveRootLab";
    private static final String HOST_PACKAGE = "com.baatiku.omnilive";
    private static final String HOST_SERVICE =
        "com.baatiku.omnilive.rootlabbridge.RootLabSurfaceBrokerService";
    private static final int MSG_SET_SURFACES = 1;
    private static final int MSG_CLEAR_SURFACES = 2;
    private static final int MSG_QUERY_STATE = 3;
    private static final int MSG_STATE = 4;
    private static final String KEY_SURFACES = "surfaces";
    private static final String KEY_READY = "ready";

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static Messenger remote;
    private static volatile boolean brokerReady;
    private static final Messenger LOCAL = new Messenger(new Handler(Looper.getMainLooper(), message -> {
        if (message.what == MSG_STATE) {
            brokerReady = message.getData().getBoolean(KEY_READY, false);
            Log.i(TAG, "rootlab broker ready=" + brokerReady);
            return true;
        }
        return false;
    }));
    private static Context appContext;
    private static boolean binding;
    private static ArrayList<Surface> pendingSurfaces = new ArrayList<>();
    private static boolean pendingClear;

    private static final ServiceConnection CONNECTION = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            remote = new Messenger(binder);
            binding = false;
            Log.i(TAG, "rootlab surface broker connected");
            queryState();
            flush();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            remote = null;
            binding = false;
            brokerReady = false;
            Log.w(TAG, "rootlab surface broker disconnected");
        }

        @Override
        public void onBindingDied(ComponentName name) {
            remote = null;
            binding = false;
            brokerReady = false;
            Log.w(TAG, "rootlab surface broker binding died");
            ensureConnected();
        }

        @Override
        public void onNullBinding(ComponentName name) {
            remote = null;
            binding = false;
            brokerReady = false;
            Log.w(TAG, "rootlab surface broker returned null binding");
        }
    };

    private RootLabSurfaceBrokerClient() {}

    static void ensureConnected() {
        MAIN.post(() -> {
            if (remote != null || binding) return;
            Context context = applicationContext();
            if (context == null) {
                Log.w(TAG, "application context unavailable for surface broker bind");
                return;
            }
            Intent intent = new Intent();
            intent.setComponent(new ComponentName(HOST_PACKAGE, HOST_SERVICE));
            try {
                binding = context.bindService(intent, CONNECTION, Context.BIND_AUTO_CREATE);
                if (!binding) Log.w(TAG, "surface broker bind request was rejected");
            } catch (Throwable t) {
                binding = false;
                Log.w(TAG, "surface broker bind failed", t);
            }
        });
    }

    static boolean isReadyForRedirect() {
        return remote != null && brokerReady;
    }

    private static void queryState() {
        Messenger target = remote;
        if (target == null) return;
        try {
            Message message = Message.obtain(null, MSG_QUERY_STATE);
            message.replyTo = LOCAL;
            target.send(message);
        } catch (Throwable t) {
            brokerReady = false;
            Log.w(TAG, "surface broker state query failed", t);
        }
    }

    static void publishSurfaces(List<Surface> surfaces) {
        ArrayList<Surface> copy = new ArrayList<>();
        if (surfaces != null) {
            for (Surface surface : surfaces) {
                if (surface != null && surface.isValid()) copy.add(surface);
            }
        }
        MAIN.post(() -> {
            pendingSurfaces = copy;
            pendingClear = false;
            ensureConnected();
            flush();
        });
    }

    static void clearSurfaces() {
        MAIN.post(() -> {
            pendingSurfaces.clear();
            pendingClear = true;
            ensureConnected();
            flush();
        });
    }

    private static void flush() {
        Messenger target = remote;
        if (target == null) return;
        try {
            if (pendingClear) {
                Message clear = Message.obtain(null, MSG_CLEAR_SURFACES);
                clear.replyTo = LOCAL;
                target.send(clear);
                pendingClear = false;
            }
            if (!pendingSurfaces.isEmpty()) {
                Bundle bundle = new Bundle();
                bundle.putParcelableArrayList(KEY_SURFACES, new ArrayList<>(pendingSurfaces));
                Message message = Message.obtain(null, MSG_SET_SURFACES);
                message.replyTo = LOCAL;
                message.setData(bundle);
                target.send(message);
            }
        } catch (Throwable t) {
            remote = null;
            binding = false;
            brokerReady = false;
            Log.w(TAG, "surface broker message failed", t);
        }
    }

    private static Context applicationContext() {
        if (appContext != null) return appContext;
        try {
            Class<?> activityThread = Class.forName("android.app.ActivityThread");
            Method currentApplication = activityThread.getDeclaredMethod("currentApplication");
            currentApplication.setAccessible(true);
            Object value = currentApplication.invoke(null);
            if (value instanceof Application) {
                appContext = ((Application) value).getApplicationContext();
            }
        } catch (Throwable t) {
            Log.w(TAG, "unable to resolve target application context", t);
        }
        return appContext;
    }
}
