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
import com.baatiku.omnilive.stream.LiveStreamService

class MainActivity : ComponentActivity() {
    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) cameraPermission.launch(Manifest.permission.CAMERA)
        setContent {
            MaterialTheme {
                var running by remember { mutableStateOf(false) }
                var profile by remember { mutableStateOf("balanced") }
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("OmniLive", style = MaterialTheme.typography.headlineLarge)
                        Text("Mobile AI camera test client")
                        Text("Quality")
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf("low_latency","balanced","high").forEach { item ->
                                FilterChip(selected = profile == item, onClick = { profile = item }, label = { Text(item) })
                            }
                        }
                        Button(onClick = {
                            if (!running) {
                                ContextCompat.startForegroundService(this@MainActivity, Intent(this@MainActivity, LiveStreamService::class.java).putExtra("profile", profile))
                            } else stopService(Intent(this@MainActivity, LiveStreamService::class.java))
                            running = !running
                        }) { Text(if (running) "Stop live session" else "Start live session") }
                        Text(if (running) "Session active" else "Idle")
                    }
                }
            }
        }
    }
}
