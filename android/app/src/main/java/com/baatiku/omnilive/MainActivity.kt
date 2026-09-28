package com.baatiku.omnilive

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.baatiku.omnilive.diagnostics.DeviceDiagnostics
import com.baatiku.omnilive.diagnostics.LiveDiagnostics
import com.baatiku.omnilive.net.SessionApi
import com.baatiku.omnilive.stream.LiveStreamService
import com.baatiku.omnilive.stream.LiveTelemetry
import com.baatiku.omnilive.stream.ReferenceImageStore
import com.baatiku.omnilive.ui.ReturnedVideoView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Locale

class MainActivity:ComponentActivity() {
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            MaterialTheme {
                val prefs=remember { getSharedPreferences("omnilive",MODE_PRIVATE) }
                val scope=rememberCoroutineScope()
                var running by remember { mutableStateOf(false) }
                var profile by remember { mutableStateOf("balanced") }
                var effect by remember { mutableStateOf("original") }
                var backendUrl by remember {
                    mutableStateOf(
                        prefs.getString("backend_url",BuildConfig.DEFAULT_BACKEND_URL)
                            ?: BuildConfig.DEFAULT_BACKEND_URL
                    )
                }
                var referencePath by remember {
                    mutableStateOf(prefs.getString("reference_image_path",null))
                }
                var referencePreview by remember {
                    mutableStateOf(ReferenceImageStore.preview(referencePath)?.asImageBitmap())
                }
                var referencePickerStatus by remember { mutableStateOf<String?>(null) }
                var backendChecking by remember { mutableStateOf(false) }
                var backendCheckStatus by remember { mutableStateOf<String?>(null) }
                var diagnosticsStatus by remember { mutableStateOf<String?>(null) }
                var cameraGranted by remember {
                    mutableStateOf(
                        ContextCompat.checkSelfPermission(this@MainActivity,Manifest.permission.CAMERA)
                            == PackageManager.PERMISSION_GRANTED
                    )
                }
                val cameraPermissionLauncher=rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestPermission()
                ) { granted ->
                    cameraGranted=granted
                }
                LaunchedEffect(Unit) {
                    if(!cameraGranted) cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                }

                val referencePicker=rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
                    if(uri!=null) {
                        scope.launch {
                            referencePickerStatus="Preparing reference image…"
                            try {
                                val imported=withContext(Dispatchers.IO) {
                                    ReferenceImageStore.importImage(this@MainActivity,uri)
                                }
                                referencePath=imported.path
                                prefs.edit().putString("reference_image_path",imported.path).apply()
                                referencePreview=withContext(Dispatchers.IO) {
                                    ReferenceImageStore.preview(imported.path)?.asImageBitmap()
                                }
                                referencePickerStatus=
                                    imported.width.toString()+"×"+imported.height+" · "+(imported.bytes/1024L)+" KB"
                                LiveTelemetry.update {
                                    it.copy(
                                        referenceImageStatus="selected",
                                        referenceImageWidth=imported.width,
                                        referenceImageHeight=imported.height,
                                        referenceImageError=null
                                    )
                                }
                            } catch(t:Throwable) {
                                referencePickerStatus=t.message ?: "Unable to import reference image"
                            }
                        }
                    }
                }

                val telemetry by LiveTelemetry.state.collectAsStateWithLifecycle()
                val deviceSnapshot=remember(cameraGranted) {
                    DeviceDiagnostics.snapshot(this@MainActivity)
                }
                val scroll=rememberScrollState()

                Surface(Modifier.fillMaxSize()) {
                    Column(
                        Modifier.fillMaxSize().verticalScroll(scroll).padding(16.dp),
                        verticalArrangement=Arrangement.spacedBy(8.dp)
                    ) {
                        Text("OmniLive",style=MaterialTheme.typography.headlineLarge)
                        if(running) ReturnedVideoView(Modifier.fillMaxWidth().aspectRatio(16f/9f))

                        OutlinedTextField(
                            value=backendUrl,
                            onValueChange={
                                backendUrl=it
                                prefs.edit().putString("backend_url",it).apply()
                            },
                            label={Text("Backend URL")},
                            singleLine=true,
                            enabled=!running,
                            modifier=Modifier.fillMaxWidth()
                        )

                        Text("Device preflight",style=MaterialTheme.typography.titleMedium)
                        Text(
                            "App "+deviceSnapshot.appVersion+" · build "+
                                deviceSnapshot.buildCommit.take(12)+" · API "+deviceSnapshot.apiLevel
                        )
                        Text(
                            "Device: "+deviceSnapshot.manufacturer+" "+deviceSnapshot.model+
                                " · camera "+if(deviceSnapshot.cameraGranted) "granted" else "missing"
                        )
                        Text("Root lab compiled: "+if(deviceSnapshot.rootLabEnabled) "yes" else "no")
                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                onClick={
                                    if(!backendChecking) {
                                        backendChecking=true
                                        backendCheckStatus="Checking backend…"
                                        scope.launch {
                                            val result=runCatching {
                                                withContext(Dispatchers.IO) {
                                                    SessionApi.health(backendUrl)
                                                }
                                            }
                                            backendCheckStatus=result.fold(
                                                onSuccess={ health ->
                                                    if(health.ok) {
                                                        "Backend OK · "+health.status+
                                                            " · model "+when {
                                                                !health.modelConfigured -> "not configured"
                                                                health.modelLoaded -> "loaded"
                                                                else -> "unavailable"
                                                            }+
                                                            (if(health.providers.isNotEmpty())
                                                                " · "+health.providers.joinToString("/") else "")
                                                    } else {
                                                        "Backend not ready · "+health.status
                                                    }
                                                },
                                                onFailure={ error ->
                                                    "Backend check failed: "+(error.message ?: error.javaClass.simpleName)
                                                }
                                            )
                                            backendChecking=false
                                        }
                                    }
                                },
                                enabled=!backendChecking && backendUrl.isNotBlank()
                            ) { Text(if(backendChecking) "Checking…" else "Test backend") }
                            OutlinedButton(
                                onClick={
                                    val clipboard=getSystemService(ClipboardManager::class.java)
                                    clipboard.setPrimaryClip(
                                        ClipData.newPlainText(
                                            "OmniLive diagnostics",
                                            buildString {
                                                appendLine(
                                                    deviceSnapshot.asText(
                                                        backendUrl=backendUrl,
                                                        referenceSelected=referencePath!=null
                                                    )
                                                )
                                                appendLine(
                                                    "Backend preflight: "+
                                                        (backendCheckStatus ?: "not run")
                                                )
                                                appendLine()
                                                append(LiveDiagnostics.asText(telemetry))
                                            }
                                        )
                                    )
                                    diagnosticsStatus="Diagnostics copied."
                                }
                            ) { Text("Copy diagnostics") }
                        }
                        backendCheckStatus?.let {
                            Text(
                                it,
                                color=if(it.startsWith("Backend OK"))
                                    MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.error
                            )
                        }
                        diagnosticsStatus?.let { Text(it) }

                        Text("Reference image",style=MaterialTheme.typography.titleMedium)
                        referencePreview?.let { preview ->
                            Image(
                                bitmap=preview,
                                contentDescription="Selected reference image",
                                contentScale=ContentScale.Fit,
                                modifier=Modifier.fillMaxWidth().heightIn(max=220.dp)
                            )
                        }
                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            Button(
                                onClick={ referencePicker.launch("image/*") },
                                enabled=!running
                            ) {
                                Text(if(referencePath==null) "Choose image" else "Change image")
                            }
                            if(referencePath!=null) {
                                OutlinedButton(
                                    onClick={
                                        ReferenceImageStore.clear(referencePath)
                                        referencePath=null
                                        referencePreview=null
                                        referencePickerStatus=null
                                        prefs.edit().remove("reference_image_path").apply()
                                        LiveTelemetry.update {
                                            it.copy(
                                                referenceImageStatus="none",
                                                referenceImageWidth=null,
                                                referenceImageHeight=null,
                                                referenceImageError=null
                                            )
                                        }
                                    },
                                    enabled=!running
                                ) { Text("Remove") }
                            }
                        }
                        if(referencePath==null) {
                            Text("No reference image selected.")
                        } else {
                            Text(referencePickerStatus ?: "Reference image saved locally.")
                        }
                        if(running || telemetry.referenceImageStatus!="none") {
                            Text(
                                "Reference upload: "+telemetry.referenceImageStatus+
                                    (if(telemetry.referenceImageWidth!=null && telemetry.referenceImageHeight!=null)
                                        " · "+telemetry.referenceImageWidth+"×"+telemetry.referenceImageHeight else "")
                            )
                            telemetry.referenceImageError?.let {
                                Text("Reference: "+it,color=MaterialTheme.colorScheme.error)
                            }
                        }

                        Text("Quality")
                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            listOf("low_latency","balanced","high").forEach { item ->
                                FilterChip(selected=profile==item,onClick={profile=item},enabled=!running,label={Text(item)})
                            }
                        }

                        Text("Visual effect")
                        val effects=listOf(
                            "original" to "original",
                            "soft_focus" to "soft",
                            "monochrome" to "mono",
                            "pixelate" to "pixel",
                            "custom_onnx" to "model"
                        )
                        Column(verticalArrangement=Arrangement.spacedBy(6.dp)) {
                            effects.chunked(3).forEach { row ->
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    row.forEach { item ->
                                        FilterChip(
                                            selected=effect==item.first,
                                            onClick={effect=item.first},
                                            enabled=!running,
                                            label={Text(item.second)}
                                        )
                                    }
                                }
                            }
                        }

                        if(!cameraGranted) {
                            Text(
                                "Camera permission is required before a live session can start.",
                                color=MaterialTheme.colorScheme.error
                            )
                            OutlinedButton(
                                onClick={ cameraPermissionLauncher.launch(Manifest.permission.CAMERA) }
                            ) { Text("Grant camera permission") }
                        }

                        Button(
                            onClick={
                                if(!running) {
                                    val permissionNow=
                                        ContextCompat.checkSelfPermission(
                                            this@MainActivity,
                                            Manifest.permission.CAMERA
                                        )==PackageManager.PERMISSION_GRANTED
                                    if(!permissionNow) {
                                        cameraGranted=false
                                        cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                                    } else {
                                        val intent=Intent(this@MainActivity,LiveStreamService::class.java)
                                            .putExtra("profile",profile)
                                            .putExtra("effect",effect)
                                            .putExtra("backend_url",backendUrl)
                                        referencePath?.let { intent.putExtra("reference_image_path",it) }
                                        ContextCompat.startForegroundService(this@MainActivity,intent)
                                        running=true
                                    }
                                } else {
                                    stopService(Intent(this@MainActivity,LiveStreamService::class.java))
                                    running=false
                                }
                            },
                            enabled=running || cameraGranted
                        ) { Text(if(running) "Stop live session" else "Start live session") }

                        Text(
                            "Connection: "+telemetry.connectionState+" · ICE: "+telemetry.iceState+
                                " · Network: "+telemetry.networkState
                        )
                        Text("Quality: "+telemetry.activeProfile+" · Effect: "+telemetry.activeEffect)
                        Text(
                            "Frame RT p50/p95: "+(telemetry.frameLatencyP50Ms?.toString() ?: "—")+"/"+
                                (telemetry.frameLatencyP95Ms?.toString() ?: "—")+" ms · n="+telemetry.frameLatencySamples
                        )
                        Text(
                            "Correlation misses: "+telemetry.frameCorrelationMisses+" · Out-of-order: "+
                                telemetry.frameCorrelationOutOfOrder+" · Backend frame: "+
                                (telemetry.backendFrameTotalMs?.toString() ?: "—")+" ms"
                        )
                        Text(
                            "RTT: "+(telemetry.rttMs?.toString() ?: "—")+" ms · Loss: "+
                                (telemetry.packetLoss?.let { String.format(Locale.US,"%.1f%%",it*100) } ?: "—")
                        )
                        Text(
                            "ICE path: "+(telemetry.iceLocalCandidateType ?: "—")+" → "+
                                (telemetry.iceRemoteCandidateType ?: "—")+" · "+
                                (telemetry.iceTransport ?: "—")+" · "+
                                when(telemetry.relayUsed) { true -> "relay"; false -> "direct"; null -> "—" }
                        )
                        Text(
                            "Video kbps tx/rx: "+(telemetry.sentKbps?.toString() ?: "—")+"/"+
                                (telemetry.receivedKbps?.toString() ?: "—")+" · target "+
                                (telemetry.targetBitrateKbps?.toString() ?: "—")+" · available "+
                                (telemetry.availableOutgoingKbps?.toString() ?: "—")
                        )
                        Text(
                            "Encoder: "+(telemetry.encoderFps?.let { String.format(Locale.US,"%.1f fps",it) } ?: "—")+
                                " · tier "+(telemetry.bitrateTier ?: "—")+" · encoded/sent "+
                                (telemetry.framesEncoded?.toString() ?: "—")+"/"+(telemetry.framesSent?.toString() ?: "—")
                        )
                        Text(
                            "Device: battery "+(telemetry.batteryPercent?.let { "$it%" } ?: "—")+
                                " · "+when(telemetry.batteryCharging) { true -> "charging"; false -> "battery"; null -> "—" }+
                                " · "+(telemetry.batteryTemperatureC?.let { String.format(Locale.US,"%.1f°C",it) } ?: "—")+
                                " · thermal "+telemetry.thermalStatus
                        )
                        Text(
                            "Backend p95: "+(telemetry.backendProcessorP95Ms?.toString() ?: "—")+" ms · Model: "+
                                (telemetry.backendModelP95Ms?.toString() ?: "—")+" ms"
                        )
                        Text(
                            "Detect p95: "+(telemetry.backendDetectorP95Ms?.toString() ?: "—")+" ms · Neural: "+
                                (telemetry.targetNeuralFps?.let { String.format(Locale.US,"%.1f fps",it) } ?: "—")
                        )
                        Text(
                            "Frames: "+(telemetry.framesReceived?.toString() ?: "—")+" · Inference skips: "+
                                (telemetry.droppedInferenceFrames?.toString() ?: "—")+" · Input drops: "+
                                (telemetry.inputOverwrittenFrames?.toString() ?: "—")
                        )
                        telemetry.lastError?.let { Text("Status: "+it,color=MaterialTheme.colorScheme.error) }
                    }
                }
            }
        }
    }
}
