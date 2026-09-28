package com.baatiku.omnilive.rootlabbridge;

import android.util.Log;
import android.view.Surface;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Target-process state only. No object here crosses into the normal OmniLive process.
 *
 * The later Binder broker consumes normalized events/surfaces from this boundary. Keeping this
 * state small also lets camera hooks fail open without holding vendor Camera2 objects indefinitely.
 */
public final class TargetCameraEventState {
    private static final String TAG = "OmniLiveRootLab";
    private static final AtomicLong SEQUENCE = new AtomicLong();
    private static final AtomicInteger OPEN_REQUESTS = new AtomicInteger();
    private static final AtomicInteger SESSION_REQUESTS = new AtomicInteger();
    private static final AtomicInteger CLOSE_EVENTS = new AtomicInteger();

    private static volatile int managerHooks;
    private static volatile int sessionHooks;
    private static volatile int closeHooks;
    private static volatile String lastCameraId = "";
    private static volatile int lastSurfaceCount;
    private static volatile String lastError = "";

    private TargetCameraEventState() {}

    public static void markHooksInstalled(int manager, int session, int close) {
        managerHooks = manager;
        sessionHooks = session;
        closeHooks = close;
        lastError = "";
        Log.i(TAG, "hook inventory manager=" + manager + " session=" + session + " close=" + close);
    }

    public static void markHookFailure(Throwable error) {
        lastError = error == null ? "unknown hook error" :
            (error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
    }

    public static void onCameraOpenRequested(String cameraId) {
        long sequence = SEQUENCE.incrementAndGet();
        OPEN_REQUESTS.incrementAndGet();
        lastCameraId = cameraId == null ? "" : cameraId;
        Log.i(TAG, "camera-open seq=" + sequence + " id=" + lastCameraId);
    }

    public static void onCaptureSessionRequested(List<Surface> surfaces) {
        long sequence = SEQUENCE.incrementAndGet();
        SESSION_REQUESTS.incrementAndGet();
        int count = surfaces == null ? 0 : surfaces.size();
        lastSurfaceCount = count;
        Log.i(TAG, "capture-session seq=" + sequence + " outputs=" + count);
    }

    public static void onCameraPathClosed(String source) {
        long sequence = SEQUENCE.incrementAndGet();
        CLOSE_EVENTS.incrementAndGet();
        Log.i(TAG, "camera-close seq=" + sequence + " source=" + source);
    }

    public static Snapshot snapshot() {
        return new Snapshot(
            SEQUENCE.get(),
            OPEN_REQUESTS.get(),
            SESSION_REQUESTS.get(),
            CLOSE_EVENTS.get(),
            managerHooks,
            sessionHooks,
            closeHooks,
            lastCameraId,
            lastSurfaceCount,
            lastError
        );
    }

    public record Snapshot(
        long sequence,
        int openRequests,
        int sessionRequests,
        int closeEvents,
        int managerHooks,
        int sessionHooks,
        int closeHooks,
        String lastCameraId,
        int lastSurfaceCount,
        String lastError
    ) {}
}
