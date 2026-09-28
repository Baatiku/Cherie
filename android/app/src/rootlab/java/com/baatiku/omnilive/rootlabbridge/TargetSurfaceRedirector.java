package com.baatiku.omnilive.rootlabbridge;

import android.graphics.ImageFormat;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.util.Size;
import android.view.Surface;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Set;

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
    private static final IdentityHashMap<OutputConfiguration, OutputConfiguration> CONFIG_REPLACEMENTS =
        new IdentityHashMap<>();
    private static final Set<Surface> UNSAFE_TARGETS =
        Collections.newSetFromMap(new IdentityHashMap<>());

    private static HandlerThread drainThread;
    private static Handler drainHandler;

    private TargetSurfaceRedirector() {}

    static synchronized Object[] redirectSessionArgs(Object[] args) {
        if (args == null || args.length == 0) return null;

        for (int index = 0; index < args.length; index++) {
            Object value = args[index];

            if (value instanceof SessionConfiguration) {
                SessionConfiguration redirected = redirectSessionConfiguration(
                    (SessionConfiguration) value
                );
                if (redirected == null) return null;
                Object[] next = args.clone();
                next[index] = redirected;
                return next;
            }

            if (!(value instanceof List<?>)) continue;
            List<?> list = (List<?>) value;
            if (list.isEmpty()) continue;

            if (allInstancesOf(list, Surface.class)) {
                ArrayList<Surface> originals = new ArrayList<>();
                for (Object item : list) {
                    Surface surface = (Surface) item;
                    if (surface != null && surface.isValid()) originals.add(surface);
                }
                if (originals.isEmpty() || !safeToRedirect(originals)) return null;

                ArrayList<Surface> dummies = new ArrayList<>(originals.size());
                try {
                    for (Surface original : originals) {
                        dummies.add(ensureReplacement(original).surface);
                    }
                } catch (Throwable t) {
                    Log.w(TAG, "raw Surface session redirect preparation failed", t);
                    reset();
                    return null;
                }

                Object[] next = args.clone();
                next[index] = dummies;
                return next;
            }

            if (allInstancesOf(list, OutputConfiguration.class)) {
                @SuppressWarnings("unchecked")
                List<OutputConfiguration> outputs = (List<OutputConfiguration>) list;
                List<OutputConfiguration> redirected = redirectOutputConfigurations(outputs);
                if (redirected == null) return null;
                Object[] next = args.clone();
                next[index] = redirected;
                return next;
            }
        }
        return null;
    }

    static synchronized Object[] substituteSessionMethodArgs(Object[] args) {
        if (args == null || args.length == 0) return null;
        Object[] next = null;

        for (int index = 0; index < args.length; index++) {
            Object value = args[index];
            Object replacement = null;

            if (value instanceof Surface) {
                Replacement mapped = REPLACEMENTS.get((Surface) value);
                replacement = mapped == null ? null : mapped.surface;
            } else if (value instanceof OutputConfiguration) {
                replacement = CONFIG_REPLACEMENTS.get((OutputConfiguration) value);
            } else if (value instanceof List<?>) {
                List<?> list = (List<?>) value;
                if (!list.isEmpty() && allInstancesOf(list, Surface.class)) {
                    ArrayList<Surface> mappedList = new ArrayList<>(list.size());
                    boolean changed = false;
                    for (Object item : list) {
                        Surface original = (Surface) item;
                        Replacement mapped = REPLACEMENTS.get(original);
                        mappedList.add(mapped == null ? original : mapped.surface);
                        changed |= mapped != null;
                    }
                    if (changed) replacement = mappedList;
                } else if (!list.isEmpty() && allInstancesOf(list, OutputConfiguration.class)) {
                    ArrayList<OutputConfiguration> mappedList = new ArrayList<>(list.size());
                    boolean changed = false;
                    for (Object item : list) {
                        OutputConfiguration original = (OutputConfiguration) item;
                        OutputConfiguration mapped = CONFIG_REPLACEMENTS.get(original);
                        mappedList.add(mapped == null ? original : mapped);
                        changed |= mapped != null;
                    }
                    if (changed) replacement = mappedList;
                }
            }

            if (replacement != null && replacement != value) {
                if (next == null) next = args.clone();
                next[index] = replacement;
            }
        }
        return next;
    }

    static synchronized Surface substituteTarget(Surface original, boolean create) {
        if (original == null) return null;
        Replacement replacement = REPLACEMENTS.get(original);
        if (replacement == null && create) {
            try {
                replacement = ensureReplacement(original);
            } catch (Throwable t) {
                Log.w(TAG, "request target redirect preparation failed", t);
                return original;
            }
        }
        return replacement == null ? original : replacement.surface;
    }

    static synchronized void markUnsafeTarget(Surface original) {
        if (original != null) UNSAFE_TARGETS.add(original);
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
        CONFIG_REPLACEMENTS.clear();
        UNSAFE_TARGETS.clear();
        Log.i(TAG, "target surface redirector reset");
    }

    private static SessionConfiguration redirectSessionConfiguration(
        SessionConfiguration original
    ) {
        try {
            List<OutputConfiguration> outputs = redirectOutputConfigurations(
                original.getOutputConfigurations()
            );
            if (outputs == null || outputs.isEmpty()) return null;

            SessionConfiguration redirected = new SessionConfiguration(
                original.getSessionType(),
                outputs,
                original.getExecutor(),
                original.getStateCallback()
            );

            try {
                if (original.getInputConfiguration() != null) {
                    redirected.setInputConfiguration(original.getInputConfiguration());
                }
            } catch (Throwable t) {
                Log.w(TAG, "unable to preserve SessionConfiguration input", t);
            }

            try {
                if (original.getSessionParameters() != null) {
                    redirected.setSessionParameters(original.getSessionParameters());
                }
            } catch (Throwable t) {
                Log.w(TAG, "unable to preserve SessionConfiguration parameters", t);
            }

            copyOptionalProperty(
                original,
                redirected,
                "getColorSpace",
                "setColorSpace"
            );
            return redirected;
        } catch (Throwable t) {
            Log.w(TAG, "SessionConfiguration redirect preparation failed", t);
            reset();
            return null;
        }
    }

    private static List<OutputConfiguration> redirectOutputConfigurations(
        List<OutputConfiguration> originals
    ) {
        if (originals == null || originals.isEmpty()) return null;

        ArrayList<Surface> allOriginalSurfaces = new ArrayList<>();
        for (OutputConfiguration output : originals) {
            if (output == null) return null;
            List<Surface> surfaces;
            try {
                surfaces = output.getSurfaces();
            } catch (Throwable ignored) {
                Surface surface = output.getSurface();
                surfaces = surface == null ? Collections.emptyList() : Collections.singletonList(surface);
            }
            if (surfaces == null || surfaces.isEmpty()) {
                Log.i(TAG, "deferred/empty OutputConfiguration left on physical camera");
                return null;
            }
            for (Surface surface : surfaces) {
                if (surface == null || !surface.isValid()) return null;
                allOriginalSurfaces.add(surface);
            }
        }

        if (!safeToRedirect(allOriginalSurfaces)) return null;

        ArrayList<OutputConfiguration> redirected = new ArrayList<>(originals.size());
        try {
            for (OutputConfiguration original : originals) {
                List<Surface> surfaces;
                try {
                    surfaces = original.getSurfaces();
                } catch (Throwable ignored) {
                    Surface surface = original.getSurface();
                    surfaces = surface == null ? Collections.emptyList() : Collections.singletonList(surface);
                }

                Surface firstDummy = ensureReplacement(surfaces.get(0)).surface;
                OutputConfiguration copy = new OutputConfiguration(
                    original.getSurfaceGroupId(),
                    firstDummy
                );

                if (surfaces.size() > 1) {
                    copy.enableSurfaceSharing();
                    for (int i = 1; i < surfaces.size(); i++) {
                        copy.addSurface(ensureReplacement(surfaces.get(i)).surface);
                    }
                }

                copyOptionalProperty(original, copy, "getPhysicalCameraId", "setPhysicalCameraId");
                copyOptionalProperty(original, copy, "getDynamicRangeProfile", "setDynamicRangeProfile");
                copyOptionalProperty(original, copy, "getStreamUseCase", "setStreamUseCase");
                copyOptionalProperty(original, copy, "getTimestampBase", "setTimestampBase");
                copyOptionalProperty(original, copy, "getMirrorMode", "setMirrorMode");
                copyOptionalProperty(original, copy, "isReadoutTimestampEnabled", "setReadoutTimestampEnabled");
                CONFIG_REPLACEMENTS.put(original, copy);
                redirected.add(copy);
            }
            return redirected;
        } catch (Throwable t) {
            Log.w(TAG, "OutputConfiguration redirect preparation failed", t);
            reset();
            return null;
        }
    }

    private static boolean safeToRedirect(List<Surface> originals) {
        for (Surface original : originals) {
            if (UNSAFE_TARGETS.contains(original)) {
                Log.w(
                    TAG,
                    "session redirect bypassed: request target was built before broker readiness"
                );
                return false;
            }
        }
        return true;
    }

    private static boolean allInstancesOf(List<?> values, Class<?> type) {
        if (values.isEmpty()) return false;
        for (Object value : values) {
            if (!type.isInstance(value)) return false;
        }
        return true;
    }

    private static void copyOptionalProperty(
        Object source,
        Object target,
        String getterName,
        String setterName
    ) {
        try {
            Method getter = source.getClass().getMethod(getterName);
            Object value = getter.invoke(source);
            Method setter = findSingleArgumentMethod(target.getClass(), setterName, getter.getReturnType());
            if (setter != null) setter.invoke(target, value);
        } catch (NoSuchMethodException ignored) {
            // Property is not available on this Android release.
        } catch (Throwable t) {
            Log.w(TAG, "optional camera metadata copy failed for " + getterName, t);
        }
    }

    private static Method findSingleArgumentMethod(
        Class<?> type,
        String name,
        Class<?> expectedType
    ) {
        for (Method method : type.getMethods()) {
            if (!name.equals(method.getName()) || method.getParameterCount() != 1) continue;
            Class<?> parameter = method.getParameterTypes()[0];
            if (wrap(parameter).isAssignableFrom(wrap(expectedType))) return method;
        }
        return null;
    }

    private static Class<?> wrap(Class<?> type) {
        if (!type.isPrimitive()) return type;
        if (type == boolean.class) return Boolean.class;
        if (type == byte.class) return Byte.class;
        if (type == short.class) return Short.class;
        if (type == int.class) return Integer.class;
        if (type == long.class) return Long.class;
        if (type == float.class) return Float.class;
        if (type == double.class) return Double.class;
        if (type == char.class) return Character.class;
        return type;
    }

    private static Replacement ensureReplacement(Surface original) {
        Replacement existing = REPLACEMENTS.get(original);
        if (existing != null) return existing;

        ensureDrainThread();
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
        Log.i(
            TAG,
            "mapped Zoom surface " + size.getWidth() + "x" + size.getHeight()
                + " -> dummy PRIVATE ImageReader"
        );
        return replacement;
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
