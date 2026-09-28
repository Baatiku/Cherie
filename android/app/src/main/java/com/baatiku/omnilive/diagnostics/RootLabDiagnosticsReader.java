package com.baatiku.omnilive.diagnostics;

import com.baatiku.omnilive.BuildConfig;
import java.lang.reflect.Method;

public final class RootLabDiagnosticsReader {
    private static final String CLASS_NAME =
        "com.baatiku.omnilive.rootlabbridge.RootLabBrokerDiagnostics";

    private RootLabDiagnosticsReader() {}

    public static RootLabRuntimeDiagnostics read() {
        if (!BuildConfig.ROOT_LAB_ENABLED) return null;
        try {
            Class<?> type = Class.forName(CLASS_NAME);
            Method summaryMethod = type.getMethod("summaryText");
            Method jsonMethod = type.getMethod("snapshotJson");
            Object summaryValue = summaryMethod.invoke(null);
            Object jsonValue = jsonMethod.invoke(null);
            String summary = summaryValue == null ? "" : summaryValue.toString();
            String json = jsonValue == null ? "" : jsonValue.toString();
            if (summary.isBlank()) summary = "Rootlab diagnostics unavailable";
            return new RootLabRuntimeDiagnostics(summary, json);
        } catch (Throwable error) {
            String detail = error.getMessage();
            if (detail == null || detail.isBlank()) detail = error.getClass().getSimpleName();
            return new RootLabRuntimeDiagnostics("Rootlab diagnostics error: " + detail, "");
        }
    }
}
