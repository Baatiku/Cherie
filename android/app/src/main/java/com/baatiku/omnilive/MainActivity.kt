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

class MainActivity : ComponentActivity() {
    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        if (ContextCompat.checkSelfPermission(this,Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
            cameraPermission.launch(Manifest.permission.CAMERA)

        setContent {
            MaterialTheme {
                var running by remember { mutableStateOf(false) }
                var profile by remember { mutableStateOf("balanced") }
                var backendUrl by remember {
                    mutableStateOf(getSharedPreferences("omnilive",MODE_PRIVATE)
                        .getString("backend_url",BuildConfig.DEFAULT_BACKEND_URL) ?: BuildConfig.DEFAULT_BACKEND_URL)
                }
                val telemetry by LiveTelemetry.state.collectAsStateWithLifecycle()

                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().padding(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                        Text("OmniLive",style=MaterialTheme.typography.headlineLarge)

                        if (running) {
                            ReturnedVideoView(Modifier.fillMaxWidth().aspectRatio(16f/9f))
                        }

                        OutlinedTextField(
                            value=backendUrl,
                            onValueChange={
                                backendUrl=it
                                getSharedPreferences("omnilive",MODE_PRIVATE).edit().putString("backend_url",it).apply()
                            },
                            label={ Text("Backend URL") },
                            singleLine=true,
                            modifier=Modifier.fillMaxWidth()
                        )

                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            listOf("low_latency","balanced","high").forEach { item ->
                                FilterChip(selected=profile==item,onClick={profile=item},label={Text(item)})
                            }
                        }

                        Button(onClick={
                            if (!running) {
                                ContextCompat.startForegroundService(
                                    this@MainActivity,
                                    Intent(this@MainActivity,LiveStreamService::class.java)
                                        .putExtra("profile",profile)
                                        .putExtra("backend_url",backendUrl)
                                )
                            } else stopService(Intent(this@MainActivity,LiveStreamService::class.java))
                            running=!running
                        }) { Text(if(running) "Stop live session" else "Start live session") }

                        Text("Connection: " + telemetry.connectionState)
                        Text("ICE: " + telemetry.iceState)
                        Text("RTT: " + (telemetry.rttMs?.toString() ?: "—") + " ms")
                        Text("Frames received: " + (telemetry.framesReceived?.toString() ?: "—"))
                        if (telemetry.lastError != null) {
                            Text("Error: " + telemetry.lastError, color=MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        }
    }
}
