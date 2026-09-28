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
        fun fromFile(path:String,maxBytes:Int=2_000_000):ReferenceUploadPayload {
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
    private const val MAX_SIDE=768
    private const val JPEG_QUALITY=88
    private const val FILE_NAME="reference_image.jpg"

    fun import(context:Context,uri:Uri):ImportedReferenceImage {
        val resolver=context.contentResolver
        val bounds=BitmapFactory.Options().apply { inJustDecodeBounds=true }
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it,null,bounds) }
            ?: error("Unable to open reference image")
        require(bounds.outWidth>0 && bounds.outHeight>0) { "Unsupported reference image" }

        var sample=1
        while(bounds.outWidth/sample>MAX_SIDE*2 || bounds.outHeight/sample>MAX_SIDE*2) sample*=2
        val options=BitmapFactory.Options().apply {
            inSampleSize=sample
            inPreferredConfig=Bitmap.Config.ARGB_8888
        }
        val decoded=resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it,null,options) }
            ?: error("Unable to decode reference image")

        val scale=minOf(1f,MAX_SIDE.toFloat()/maxOf(decoded.width,decoded.height).toFloat())
        val width=maxOf(32,(decoded.width*scale).toInt())
        val height=maxOf(32,(decoded.height*scale).toInt())
        val normalized=if(width==decoded.width && height==decoded.height) decoded
            else Bitmap.createScaledBitmap(decoded,width,height,true)

        val file=File(context.filesDir,FILE_NAME)
        val temp=File(context.filesDir,FILE_NAME+".tmp")
        FileOutputStream(temp).use { output ->
            check(normalized.compress(Bitmap.CompressFormat.JPEG,JPEG_QUALITY,output)) {
                "Unable to encode reference image"
            }
        }
        if(temp.length()>2_000_000L) {
            FileOutputStream(temp,false).use { output ->
                check(normalized.compress(Bitmap.CompressFormat.JPEG,72,output)) {
                    "Unable to compress reference image"
                }
            }
        }
        require(temp.length()<=2_000_000L) { "Reference image remains too large after compression" }
        if(file.exists()) file.delete()
        check(temp.renameTo(file)) { "Unable to store reference image" }

        if(normalized!==decoded) normalized.recycle()
        decoded.recycle()
        return ImportedReferenceImage(file.absolutePath,width,height,file.length())
    }

    fun preview(path:String?):Bitmap? {
        if(path.isNullOrBlank()) return null
        val file=File(path)
        if(!file.isFile) return null
        return BitmapFactory.decodeFile(file.absolutePath)
    }

    fun clear(path:String?) {
        if(path.isNullOrBlank()) return
        try { File(path).delete() } catch(_:Throwable) {}
    }
}
