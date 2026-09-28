package com.baatiku.omnicam.xposed;

import android.graphics.SurfaceTexture;
import android.hardware.Camera;
import android.view.Surface;

import java.lang.reflect.Method;
import java.util.IdentityHashMap;
import java.util.Map;

@SuppressWarnings("deprecation")
final class Camera1Virtualizer {
    private static final Map<Camera, SurfaceTexture> dummies =
            new IdentityHashMap<>();

    static void install(
            OmniCamModule module,
            SurfaceRegistry registry) throws Exception {
        Method setPreviewTexture = Camera.class.getDeclaredMethod(
                "setPreviewTexture",
                SurfaceTexture.class);

        OmniCamModule.hookProtective(
                module,
                setPreviewTexture,
                chain -> {
                    Camera camera =
                            (Camera) chain.getThisObject();
                    SurfaceTexture originalTexture =
                            (SurfaceTexture) chain.getArg(0);

                    if (originalTexture == null) {
                        return chain.proceed();
                    }

                    Surface original =
                            new Surface(originalTexture);
                    registry.replacementFor(original);

                    SurfaceTexture dummy =
                            new SurfaceTexture(false);
                    dummy.setDefaultBufferSize(640, 480);
                    synchronized (dummies) {
                        dummies.put(camera, dummy);
                    }

                    return chain.proceed(
                            new Object[]{dummy});
                });

        Method release = Camera.class.getDeclaredMethod("release");
        OmniCamModule.hookProtective(
                module,
                release,
                chain -> {
                    Camera camera =
                            (Camera) chain.getThisObject();
                    try {
                        return chain.proceed();
                    } finally {
                        SurfaceTexture t;
                        synchronized (dummies) {
                            t = dummies.remove(camera);
                        }
                        if (t != null) {
                            try { t.release(); }
                            catch (Throwable ignored) {}
                        }
                        registry.clear();
                    }
                });
    }
}
