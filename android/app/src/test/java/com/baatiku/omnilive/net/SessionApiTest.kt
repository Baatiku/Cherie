package com.baatiku.omnilive.net

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionApiTest {
    @Test
    fun parseHealthCapturesModelAndProviderState() {
        val health=SessionApi.parseHealth(
            JSONObject(
                """
                {
                  "ok": true,
                  "status": "ok",
                  "service": "omnilive",
                  "configured": true,
                  "loaded": true,
                  "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"]
                }
                """.trimIndent()
            )
        )

        assertTrue(health.ok)
        assertEquals("ok",health.status)
        assertEquals("omnilive",health.service)
        assertTrue(health.modelConfigured)
        assertTrue(health.modelLoaded)
        assertEquals(listOf("CUDAExecutionProvider","CPUExecutionProvider"),health.providers)
    }

    @Test
    fun parseHealthDefaultsMissingOptionalModelFields() {
        val health=SessionApi.parseHealth(
            JSONObject("""{"ok":false,"status":"model_unavailable","service":"omnilive"}""")
        )

        assertFalse(health.ok)
        assertFalse(health.modelConfigured)
        assertFalse(health.modelLoaded)
        assertTrue(health.providers.isEmpty())
    }
}
