package com.baatiku.omnilive.rootlabbridge;

import android.hardware.camera2.CameraManager;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.util.Log;
import android.view.Surface;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import io.github.libxposed.api.XposedModule;

/**
 * Rootlab-only modern libxposed entry point.
 *
 * This class is packaged only in -Pomnilive.rootLab=true APKs and the module scope is statically
 * restricted to Zoom. It observes Camera2 lifecycle and capture-session output surfaces so the
 * compatibility adapter can make package-scoped decisions without touching normal OmniLive.
 */
public final class OmniLiveXposedModule extends XposedModule {
    private static final String TAG = "OmniLiveRootLab";
    private static final String TARGET_PACKAGE = RootLabTargets.ZOOM_PACKAGE;
    private static final AtomicBoolean INSTALLED = new AtomicBoolean(false);

    @Override
    public void onModuleLoaded(ModuleLoadedParam param) {
        log(Log.INFO, TAG, "module loaded in " + param.getProcessName());
    }

    @Override
    public void onPackageReady(PackageReadyParam param) {
        if (!TARGET_PACKAGE.equals(param.getPackageName())) return;
        if (!INSTALLED.compareAndSet(false, true)) return;

        try {
            int managerHooks = hookCameraManager();
            int deviceHooks = hookClassMethods(
                "android.hardware.camera2.impl.CameraDeviceImpl",
                param.getClassLoader(),
                "createCaptureSession",
                "createCaptureSessionByOutputConfigurations",
                "createReprocessableCaptureSession",
                "createReprocessableCaptureSessionByConfigurations"
            );
            int deviceCloseHooks = hookClassMethods(
                "android.hardware.camera2.impl.CameraDeviceImpl",
                param.getClassLoader(),
                "close"
            );
            int sessionCloseHooks = hookClassMethods(
                "android.hardware.camera2.impl.CameraCaptureSessionImpl",
                param.getClassLoader(),
                "close"
            );

            TargetCameraEventState.markHooksInstalled(
                managerHooks,
                deviceHooks,
                deviceCloseHooks + sessionCloseHooks
            );
            log(
                Log.INFO,
                TAG,
                "Zoom Camera2 hooks ready: manager=" + managerHooks
                    + " session=" + deviceHooks
                    + " close=" + (deviceCloseHooks + sessionCloseHooks)
            );
        } catch (Throwable t) {
            TargetCameraEventState.markHookFailure(t);
            log(Log.ERROR, TAG, "failed to install Zoom Camera2 hooks", t);
        }
    }

    private int hookCameraManager() {
        int count = 0;
        for (Method method : CameraManager.class.getDeclaredMethods()) {
            if (!"openCamera".equals(method.getName())) continue;
            try {
                hook(method).intercept(chain -> {
                    Object first = chain.getArg(0);
                    String cameraId = first instanceof String ? (String) first : "unknown";
                    TargetCameraEventState.onCameraOpenRequested(cameraId);
                    RootLabSurfaceBrokerClient.ensureConnected();
                    return chain.proceed();
                });
                count++;
            } catch (Throwable t) {
                log(Log.WARN, TAG, "openCamera hook skipped: " + method, t);
            }
        }
        return count;
    }

    private int hookClassMethods(
        String className,
        ClassLoader classLoader,
        String... methodNames
    ) {
        try {
            Class<?> type = Class.forName(className, false, classLoader);
            int count = 0;
            for (Method method : type.getDeclaredMethods()) {
                if (!contains(methodNames, method.getName())) continue;
                try {
                    if (method.getName().startsWith("create")) {
                        hook(method).intercept(chain -> {
                            List<Surface> outputs = collectSurfaces(chain.getArgs().toArray());
                            TargetCameraEventState.onCaptureSessionRequested(outputs);
                            RootLabSurfaceBrokerClient.publishSurfaces(outputs);
                            return chain.proceed();
                        });
                    } else if ("close".equals(method.getName())) {
                        hook(method).intercept(chain -> {
                            TargetCameraEventState.onCameraPathClosed(className);
                            RootLabSurfaceBrokerClient.clearSurfaces();
                            return chain.proceed();
                        });
                    } else {
                        continue;
                    }
                    count++;
                } catch (Throwable t) {
                    log(Log.WARN, TAG, "hook skipped: " + className + "#" + method.getName(), t);
                }
            }
            return count;
        } catch (Throwable t) {
            log(Log.WARN, TAG, "Camera2 implementation class unavailable: " + className, t);
            return 0;
        }
    }

    static List<Surface> collectSurfaces(Object[] args) {
        ArrayList<Surface> result = new ArrayList<>();
        if (args == null) return result;
        for (Object arg : args) collectSurfaces(arg, result);
        return result;
    }

    private static void collectSurfaces(Object value, List<Surface> result) {
        if (value == null) return;
        if (value instanceof Surface) {
            addDistinct((Surface) value, result);
            return;
        }
        if (value instanceof OutputConfiguration) {
            OutputConfiguration output = (OutputConfiguration) value;
            try {
                for (Surface surface : output.getSurfaces()) addDistinct(surface, result);
            } catch (Throwable ignored) {
                try {
                    addDistinct(output.getSurface(), result);
                } catch (Throwable ignoredAgain) {
                    // Keep hook non-fatal across vendor framework variants.
                }
            }
            return;
        }
        if (value instanceof SessionConfiguration) {
            SessionConfiguration session = (SessionConfiguration) value;
            try {
                for (OutputConfiguration output : session.getOutputConfigurations()) {
                    collectSurfaces(output, result);
                }
            } catch (Throwable ignored) {
                // Vendor implementation may differ. Other args are still inspected.
            }
            return;
        }
        if (value instanceof Collection<?>) {
            for (Object item : (Collection<?>) value) collectSurfaces(item, result);
            return;
        }
        if (value.getClass().isArray() && value instanceof Object[]) {
            for (Object item : (Object[]) value) collectSurfaces(item, result);
        }
    }

    private static void addDistinct(Surface surface, List<Surface> result) {
        if (surface == null || !surface.isValid()) return;
        for (Surface existing : result) {
            if (existing == surface) return;
        }
        result.add(surface);
    }

    private static boolean contains(String[] values, String candidate) {
        for (String value : values) if (value.equals(candidate)) return true;
        return false;
    }
}
