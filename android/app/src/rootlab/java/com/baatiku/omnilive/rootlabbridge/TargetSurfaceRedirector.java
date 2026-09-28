package com.baatiku.omnilive.rootlabbridge;

import android.graphics.ImageFormat;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.util.Size;
import android.view.Surface;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;

/**
 * Target-process Camera2 redirection state.
 *
 * Original Zoom Surfaces stay owned by Zoom's consumers and are published to the OmniLive broker.
 * Camera2 receives drained PRIVATE ImageReader Surfaces instead. CaptureRequest target substitution
 * must use this same mapping so every request target remains a member of the configured session.
 */
final class TargetSurfaceRedirector {
    private static final String TAG = "OmniLiveRootLab";
    private static final int FALLBACK_WIDTH = 1280;
    private static final int FALLBACK_HEIGHT = 720;
    private static final IdentityHashMap<Surface, Replacement> REPLACEMENTS =
        new IdentityHashMap<>();

    private static HandlerThread drainThread;
    private static Handler drainHandler;

    private TargetSurfaceRedirector() {}

    static synchronized Object[] redirectSessionArgs(Object[] args) {
        if (args == null || args.length == 0) return null;

        for (int index = 0; index < args.length; index++) {
            Object value = args[index];
            if (!(value instanceof List<?>)) continue;
            List<?> list = (List<?>) value;
            if (list.isEmpty()) continue;

            ArrayList<Surface> originals = new ArrayList<>();
            boolean allSurfaces = true;
            for (Object item : list) {
                if (!(item instanceof Surface)) {
                    allSurfaces = false;
                    break;
                }
                Surface surface = (Surface) item;
                if (surface != null && surface.isValid()) originals.add(surface);
            }
            if (!allSurfaces || originals.isEmpty()) continue;

            ArrayList<Surface> dummies = prepare(originals);
            if (dummies.size() != originals.size()) {
                reset();
                return null;
            }

            Object[] redirected = args.clone();
            redirected[index] = dummies;
            return redirected;
        }
        return null;
    }

    static synchronized Surface substituteTarget(Surface original) {
        if (original == null) return null;
        Replacement replacement = REPLACEMENTS.get(original);
        return replacement == null ? original : replacement.surface;
    }

    static synchronized boolean isRedirected(Surface original) {
        return original != null && REPLACEMENTS.containsKey(original);
    }

    static synchronized int activeMappings() {
        return REPLACEMENTS.size();
    }

    static synchronized void reset() {
        for (Replacement replacement : REPLACEMENTS.values()) {
            replacement.close();
        }
        REPLACEMENTS.clear();
        Log.i(TAG, "target surface redirector reset");
    }

    private static ArrayList<Surface> prepare(List<Surface> originals) {
        reset();
        ensureDrainThread();

        ArrayList<Surface> dummies = new ArrayList<>(originals.size());
        try {
            for (Surface original : originals) {
                Size size = surfaceSize(original);
                ImageReader reader = ImageReader.newInstance(
                    Math.max(1, size.getWidth()),
                    Math.max(1, size.getHeight()),
                    ImageFormat.PRIVATE,
                    3
                );
                reader.setOnImageAvailableListener(
                    source -> drainLatest(source),
                    drainHandler
                );
                Replacement replacement = new Replacement(reader);
                REPLACEMENTS.put(original, replacement);
                dummies.add(replacement.surface);
                Log.i(
                    TAG,
                    "mapped Zoom surface " + size.getWidth() + "x" + size.getHeight()
                        + " -> dummy PRIVATE ImageReader"
                );
            }
            return dummies;
        } catch (Throwable t) {
            Log.w(TAG, "failed to prepare dummy Camera2 surfaces", t);
            reset();
            return new ArrayList<>();
        }
    }

    private static void ensureDrainThread() {
        if (drainThread != null && drainThread.isAlive() && drainHandler != null) return;
        drainThread = new HandlerThread("OmniLiveCameraDrain");
        drainThread.start();
        drainHandler = new Handler(drainThread.getLooper());
    }

    private static void drainLatest(ImageReader reader) {
        Image image = null;
        try {
            image = reader.acquireLatestImage();
        } catch (Throwable t) {
            Log.w(TAG, "dummy ImageReader drain failed", t);
        } finally {
            if (image != null) {
                try {
                    image.close();
                } catch (Throwable ignored) {}
            }
        }
    }

    private static Size surfaceSize(Surface surface) {
        try {
            Class<?> type = Class.forName("android.hardware.camera2.utils.SurfaceUtils");
            Method method = type.getDeclaredMethod("getSurfaceSize", Surface.class);
            method.setAccessible(true);
            Object value = method.invoke(null, surface);
            if (value instanceof Size) {
                Size size = (Size) value;
                if (size.getWidth() > 0 && size.getHeight() > 0) return size;
            }
        } catch (Throwable t) {
            Log.w(TAG, "surface size probe unavailable; using 1280x720 dummy", t);
        }
        return new Size(FALLBACK_WIDTH, FALLBACK_HEIGHT);
    }

    private static final class Replacement {
        final ImageReader reader;
        final Surface surface;

        Replacement(ImageReader reader) {
            this.reader = reader;
            this.surface = reader.getSurface();
        }

        void close() {
            try {
                reader.setOnImageAvailableListener(null, null);
            } catch (Throwable ignored) {}
            try {
                reader.close();
            } catch (Throwable ignored) {}
        }
    }
}
