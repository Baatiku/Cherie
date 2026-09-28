package com.baatiku.omnicam.xposed;

import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.util.Log;
import android.view.Surface;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

final class Camera2Virtualizer {
    private static final String TAG = "OmniCam";

    static void install(
            OmniCamModule module,
            SurfaceRegistry registry) throws Exception {
        Class<?> impl = Class.forName(
                "android.hardware.camera2.impl.CameraDeviceImpl");

        for (Method m : impl.getDeclaredMethods()) {
            String name = m.getName();
            if (!name.contains("CaptureSession")) continue;

            m.setAccessible(true);
            OmniCamModule.hookProtective(module, m, chain -> {
                Object[] args = chain.getArgs().toArray();
                boolean changed = rewriteArgs(args, registry);
                return changed
                        ? chain.proceed(args)
                        : chain.proceed();
            });
        }

        Method addTarget = CaptureRequest.Builder.class
                .getDeclaredMethod("addTarget", Surface.class);
        OmniCamModule.hookProtective(module, addTarget, chain -> {
            Surface original = (Surface) chain.getArg(0);
            Surface mapped = registry.mapped(original);
            if (mapped == null) return chain.proceed();
            return chain.proceed(new Object[]{mapped});
        });

        Method removeTarget = CaptureRequest.Builder.class
                .getDeclaredMethod("removeTarget", Surface.class);
        OmniCamModule.hookProtective(module, removeTarget, chain -> {
            Surface original = (Surface) chain.getArg(0);
            Surface mapped = registry.mapped(original);
            if (mapped == null) return chain.proceed();
            return chain.proceed(new Object[]{mapped});
        });

        for (Method m : impl.getDeclaredMethods()) {
            if (!"close".equals(m.getName()) ||
                    m.getParameterCount() != 0) {
                continue;
            }

            m.setAccessible(true);
            OmniCamModule.hookProtective(module, m, chain -> {
                try {
                    return chain.proceed();
                } finally {
                    registry.clear();
                }
            });
        }
    }

    private static boolean rewriteArgs(
            Object[] args,
            SurfaceRegistry registry) {
        boolean changed = false;

        for (int i = 0; i < args.length; i++) {
            Object arg = args[i];

            if (arg instanceof SessionConfiguration) {
                SessionConfiguration sc =
                        (SessionConfiguration) arg;
                List<OutputConfiguration> mapped =
                        mapOutputs(
                                sc.getOutputConfigurations(),
                                registry);

                SessionConfiguration replacement =
                        new SessionConfiguration(
                                sc.getSessionType(),
                                mapped,
                                sc.getExecutor(),
                                sc.getStateCallback());

                if (sc.getInputConfiguration() != null) {
                    replacement.setInputConfiguration(
                            sc.getInputConfiguration());
                }
                if (sc.getSessionParameters() != null) {
                    replacement.setSessionParameters(
                            sc.getSessionParameters());
                }

                args[i] = replacement;
                changed = true;
                continue;
            }

            if (!(arg instanceof List<?>)) continue;
            List<?> list = (List<?>) arg;
            if (list.isEmpty()) continue;

            Object first = list.get(0);
            if (first instanceof Surface) {
                ArrayList<Surface> mapped = new ArrayList<>();
                for (Object o : list) {
                    mapped.add(
                            registry.replacementFor(
                                    (Surface) o));
                }
                args[i] = mapped;
                changed = true;
            } else if (first instanceof OutputConfiguration) {
                @SuppressWarnings("unchecked")
                List<OutputConfiguration> outputs =
                        (List<OutputConfiguration>) list;
                args[i] = mapOutputs(outputs, registry);
                changed = true;
            }
        }

        if (changed) {
            Log.i(TAG, "Camera2 capture session virtualized");
        }
        return changed;
    }

    private static List<OutputConfiguration> mapOutputs(
            List<OutputConfiguration> source,
            SurfaceRegistry registry) {
        ArrayList<OutputConfiguration> out =
                new ArrayList<>(source.size());

        for (OutputConfiguration original : source) {
            Surface s = original.getSurface();
            if (s == null) {
                out.add(original);
                continue;
            }
            out.add(
                    new OutputConfiguration(
                            registry.replacementFor(s)));
        }
        return out;
    }
}
