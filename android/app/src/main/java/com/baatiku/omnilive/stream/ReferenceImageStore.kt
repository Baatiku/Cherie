package com.baatiku.omnilive.stream

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import java.io.File
import java.io.FileOutputStream
import java.util.Base64

data class ImportedReferenceImage(
    val path:String,
    val width:Int,
    val height:Int,
    val bytes:Long
)

data class ReferenceUploadPayload(
    val dataBase64:String,
    val bytes:Int
) {
    companion object {
        fun fromFile(path:String,maxBytes:Int=1_000_000):ReferenceUploadPayload {
            val file=File(path)
            require(file.isFile) { "Reference image file is missing" }
            val bytes=file.readBytes()
            require(bytes.isNotEmpty()) { "Reference image file is empty" }
            require(bytes.size<=maxBytes) { "Reference image exceeds upload limit" }
            return ReferenceUploadPayload(
                dataBase64=Base64.getEncoder().encodeToString(bytes),
                bytes=bytes.size
            )
        }
    }
}

object ReferenceImageStore {
    private const val MAX_SIDE=512
    private const val MAX_BYTES=700_000L
    private const val FILE_NAME="reference_image.jpg"

    private fun compress(bitmap:Bitmap,quality:Int):ByteArray {
        val stream=java.io.ByteArrayOutputStream()
        check(bitmap.compress(Bitmap.CompressFormat.JPEG,quality,stream)) {
            "Unable to encode reference image"
        }
        return stream.toByteArray()
    }

    fun importImage(context:Context,uri:Uri):ImportedReferenceImage {
        // Read the source once: cloud gallery URIs can hand out one-shot
        // streams that fail when opened a second time for a bounds pass.
        val resolver=context.contentResolver
        val original=resolver.openInputStream(uri)?.use { it.readBytes() }
            ?: error("Unable to open reference image")
        require(original.isNotEmpty()) { "Reference image file is empty" }

        val bounds=BitmapFactory.Options().apply { inJustDecodeBounds=true }
        BitmapFactory.decodeByteArray(original,0,original.size,bounds)
        require(bounds.outWidth>0 && bounds.outHeight>0) { "Unsupported reference image" }

        var sample=1
        while(bounds.outWidth/sample>MAX_SIDE*2 || bounds.outHeight/sample>MAX_SIDE*2) sample*=2
        val options=BitmapFactory.Options().apply {
            inSampleSize=sample
            inPreferredConfig=Bitmap.Config.ARGB_8888
        }
        val decoded=BitmapFactory.decodeByteArray(original,0,original.size,options)
            ?: error("Unable to decode reference image")

        // Keep the stored photo small (512px, ~0.7MB max) so the later
        // WebSocket upload stays far below typical 1MB message limits.
        val scale=minOf(1f,MAX_SIDE.toFloat()/maxOf(decoded.width,decoded.height).toFloat())
        val width=maxOf(32,(decoded.width*scale).toInt())
        val height=maxOf(32,(decoded.height*scale).toInt())
        var normalized=if(width==decoded.width && height==decoded.height) decoded
            else Bitmap.createScaledBitmap(decoded,width,height,true)

        var jpeg=compress(normalized,82)
        for(quality in listOf(70,55)) {
            if(jpeg.size<=MAX_BYTES) break
            jpeg=compress(normalized,quality)
        }
        if(jpeg.size>MAX_BYTES) {
            val smallScale=384f/maxOf(normalized.width,normalized.height).toFloat()
            val small=Bitmap.createScaledBitmap(
                normalized,
                maxOf(32,(normalized.width*smallScale).toInt()),
                maxOf(32,(normalized.height*smallScale).toInt()),
                true
            )
            if(small!==normalized) normalized.recycle()
            normalized=small
            jpeg=compress(normalized,70)
        }
        require(jpeg.size<=MAX_BYTES) { "Reference image remains too large after compression" }

        val file=File(context.filesDir,FILE_NAME)
        val temp=File(context.filesDir,FILE_NAME+".tmp")
        FileOutputStream(temp).use { it.write(jpeg) }
        if(file.exists()) file.delete()
        check(temp.renameTo(file)) { "Unable to store reference image" }

        if(normalized!==decoded) normalized.recycle()
        decoded.recycle()
        return ImportedReferenceImage(file.absolutePath,normalized.width,normalized.height,file.length())
    }

    fun preview(path:String?, maxSide:Int = 512):Bitmap? {
        if(path.isNullOrBlank()) return null
        val file=File(path)
        if(!file.isFile) return null
        // Subsampled decode so Compose preview never holds a full-res bitmap.
        val bounds=BitmapFactory.Options().apply { inJustDecodeBounds=true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if(bounds.outWidth<=0 || bounds.outHeight<=0) {
            return BitmapFactory.decodeFile(file.absolutePath)
        }
        var sample=1
        while(bounds.outWidth/sample>maxSide*2 || bounds.outHeight/sample>maxSide*2) sample*=2
        val options=BitmapFactory.Options().apply {
            inSampleSize=sample
            inPreferredConfig=Bitmap.Config.ARGB_8888
        }
        return BitmapFactory.decodeFile(file.absolutePath, options)
    }

    fun clear(path:String?) {
        if(path.isNullOrBlank()) return
        try { File(path).delete() } catch(_:Throwable) {}
    }
}
