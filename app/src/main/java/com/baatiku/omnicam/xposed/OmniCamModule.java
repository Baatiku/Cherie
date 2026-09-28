package com.baatiku.omnicam.xposed;

import android.os.Build;
import android.util.Log;

import java.lang.reflect.Executable;

import io.github.libxposed.api.XposedInterface;
import io.github.libxposed.api.XposedModule;

public final class OmniCamModule extends XposedModule {
    private static final String TARGET = "us.zoom.videomeetings";
    private static final String TAG = "OmniCam";

    @Override
    public void onModuleLoaded(ModuleLoadedParam param) {
        log(Log.INFO, TAG, "module loaded in " + param.getProcessName());
    }

    @Override
    public void onPackageLoaded(PackageLoadedParam param) {
        if (Build.VERSION.SDK_INT < 29 || !TARGET.equals(param.getPackageName())) return;

        try {
            SurfaceRegistry registry = new SurfaceRegistry();
            Camera2Virtualizer.install(this, registry);
            Camera1Virtualizer.install(this, registry);
            log(Log.INFO, TAG, "Zoom virtual-camera hooks installed");
        } catch (Throwable t) {
            log(Log.ERROR, TAG, "hook installation failed", t);
        }
    }

    static void hookProtective(
            OmniCamModule module,
            Executable executable,
            XposedInterface.Hooker hooker) {
        module.hook(executable)
                .setExceptionMode(XposedInterface.ExceptionMode.PROTECTIVE)
                .intercept(hooker);
    }
}
