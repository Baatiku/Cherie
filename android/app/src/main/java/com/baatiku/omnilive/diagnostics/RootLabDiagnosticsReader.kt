package com.baatiku.omnilive.diagnostics

import com.baatiku.omnilive.BuildConfig

data class RootLabRuntimeDiagnostics(
    val summary:String,
    val json:String
)

object RootLabDiagnosticsReader {
    private const val CLASS_NAME =
        "com.baatiku.omnilive.rootlabbridge.RootLabBrokerDiagnostics"

    fun read():RootLabRuntimeDiagnostics? {
        if(!BuildConfig.ROOT_LAB_ENABLED) return null
        return try {
            val type=Class.forName(CLASS_NAME)
            val summary=type.getMethod("summaryText").invoke(null)?.toString().orEmpty()
            val json=type.getMethod("snapshotJson").invoke(null)?.toString().orEmpty()
            RootLabRuntimeDiagnostics(
                summary=summary.ifBlank { "Rootlab diagnostics unavailable" },
                json=json
            )
        } catch(t:Throwable) {
            RootLabRuntimeDiagnostics(
                summary="Rootlab diagnostics error: "+(t.message ?: t.javaClass.simpleName),
                json=""
            )
        }
    }
}
