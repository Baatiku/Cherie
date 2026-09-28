package com.baatiku.omnilive.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.Base64

class ReferenceUploadPayloadTest {
    @Test
    fun payload_encodes_file_without_changing_bytes() {
        val file=File.createTempFile("omnilive-reference",".jpg")
        try {
            val source=byteArrayOf(1,2,3,4,5,6)
            file.writeBytes(source)
            val payload=ReferenceUploadPayload.fromFile(file.absolutePath)
            assertEquals(source.size,payload.bytes)
            assertTrue(Base64.getDecoder().decode(payload.dataBase64).contentEquals(source))
        } finally {
            file.delete()
        }
    }

    @Test(expected=IllegalArgumentException::class)
    fun payload_rejects_files_over_limit() {
        val file=File.createTempFile("omnilive-reference-large",".jpg")
        try {
            file.writeBytes(ByteArray(32))
            ReferenceUploadPayload.fromFile(file.absolutePath,maxBytes=16)
        } finally {
            file.delete()
        }
    }
}
