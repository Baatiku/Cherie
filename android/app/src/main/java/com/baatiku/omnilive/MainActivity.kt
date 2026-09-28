package com.baatiku.omnilive

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.baatiku.omnilive.stream.LiveStreamService
import com.baatiku.omnilive.stream.LiveTelemetry
import com.baatiku.omnilive.ui.ReturnedVideoView
import java.util.Locale

class MainActivity:ComponentActivity() {
    private val cameraPermission=registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        if(ContextCompat.checkSelfPermission(this,Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)
            cameraPermission.launch(Manifest.permission.CAMERA)

        setContent {
            MaterialTheme {
                var running by remember { mutableStateOf(false) }
                var profile by remember { mutableStateOf("balanced") }
                var effect by remember { mutableStateOf("original") }
                var backendUrl by remember {
                    mutableStateOf(getSharedPreferences("omnilive",MODE_PRIVATE)
                        .getString("backend_url",BuildConfig.DEFAULT_BACKEND_URL) ?: BuildConfig.DEFAULT_BACKEND_URL)
                }
                val telemetry by LiveTelemetry.state.collectAsStateWithLifecycle()

                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().padding(16.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
                        Text("OmniLive",style=MaterialTheme.typography.headlineLarge)
                        if(running) ReturnedVideoView(Modifier.fillMaxWidth().aspectRatio(16f/9f))

                        OutlinedTextField(
                            value=backendUrl,
                            onValueChange={
                                backendUrl=it
                                getSharedPreferences("omnilive",MODE_PRIVATE).edit().putString("backend_url",it).apply()
                            },
                            label={Text("Backend URL")},
                            singleLine=true,
                            modifier=Modifier.fillMaxWidth()
                        )

                        Text("Quality")
                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            listOf("low_latency","balanced","high").forEach { item ->
                                FilterChip(selected=profile==item,onClick={profile=item},label={Text(item)})
                            }
                        }

                        Text("Visual effect")
                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            listOf("original","soft_focus","monochrome","pixelate").forEach { item ->
                                FilterChip(selected=effect==item,onClick={effect=item},label={Text(item)})
                            }
                        }

                        Button(onClick={
                            if(!running) {
                                ContextCompat.startForegroundService(
                                    this@MainActivity,
                                    Intent(this@MainActivity,LiveStreamService::class.java)
                                        .putExtra("profile",profile)
                                        .putExtra("effect",effect)
                                        .putExtra("backend_url",backendUrl)
                                )
                            } else stopService(Intent(this@MainActivity,LiveStreamService::class.java))
                            running=!running
                        }) { Text(if(running) "Stop live session" else "Start live session") }

                        Text("Connection: "+telemetry.connectionState+" · ICE: "+telemetry.iceState)
                        Text("Quality: "+telemetry.activeProfile+" · Effect: "+telemetry.activeEffect)
                        Text(
                            "RTT: "+(telemetry.rttMs?.toString() ?: "—")+" ms · Loss: "+
                                (telemetry.packetLoss?.let { String.format(Locale.US,"%.1f%%",it*100) } ?: "—")
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
