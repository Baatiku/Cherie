package com.baatiku.omnilive.diagnostics

import android.content.Context
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Persistent flight recorder for the camera service lifecycle.
 *
 * StateFlows reset when the service stops, which is exactly why "idle with
 * no error" tells us nothing. This log survives restarts, so a start that
 * instantly dies still leaves footprints. Any uncaught crash is saved to a
 * separate file and shown in Settings until cleared.
 */
object ServiceLog {
    private const val LOG_NAME = "omnilive-service-log.txt"
    private const val CRASH_NAME = "omnilive-crash.txt"
    private const val MAX_LINES = 120
    private const val MAX_CRASH_CHARS = 4000
    private val handlerInstalled = AtomicBoolean(false)

    private fun logFile(context: Context) = File(context.filesDir, LOG_NAME)
    private fun crashFile(context: Context) = File(context.filesDir, CRASH_NAME)

    @Synchronized
    fun append(context: Context, tag: String, message: String) {
        try {
            val time = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
            val file = logFile(context.applicationContext)
            val lines = if (file.exists()) file.readLines().toMutableList() else mutableListOf()
            lines.add("$time [$tag] $message")
            while (lines.size > MAX_LINES) lines.removeAt(0)
            file.writeText(lines.joinToString("\n"))
        } catch (_: Throwable) {
            // Logging must never break the app.
        }
    }

    fun readTail(context: Context, n: Int = 30): String {
        return try {
            val file = logFile(context.applicationContext)
            if (!file.exists()) "(service log empty — the camera service never logged anything)"
            else file.readLines().takeLast(n).joinToString("\n").ifBlank { "(service log empty)" }
        } catch (t: Throwable) {
            "(log unreadable: ${t.message})"
        }
    }

    fun saveCrash(context: Context, throwable: Throwable) {
        try {
            crashFile(context.applicationContext)
                .writeText(throwable.stackTraceToString().take(MAX_CRASH_CHARS))
        } catch (_: Throwable) {
        }
    }

    fun readCrash(context: Context): String? {
        return try {
            crashFile(context.applicationContext)
                .takeIf { it.exists() }
                ?.readText()
                ?.takeIf { it.isNotBlank() }
        } catch (_: Throwable) {
            null
        }
    }

    fun clearCrash(context: Context) {
        try {
            crashFile(context.applicationContext).delete()
        } catch (_: Throwable) {
        }
    }

    fun installCrashHandler(context: Context) {
        if (!handlerInstalled.compareAndSet(false, true)) return
        val appContext = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                append(appContext, "crash", "${throwable.javaClass.simpleName}: ${throwable.message}")
                saveCrash(appContext, throwable)
            } catch (_: Throwable) {
            }
            if (previous != null) {
                previous.uncaughtException(thread, throwable)
            } else {
                android.os.Process.killProcess(android.os.Process.myPid())
            }
        }
    }
}
