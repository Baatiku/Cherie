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
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.baatiku.omnilive.diagnostics.DeviceDiagnostics
import com.baatiku.omnilive.diagnostics.LiveDiagnostics
import com.baatiku.omnilive.diagnostics.RootLabDiagnosticsReader
import com.baatiku.omnilive.net.TransportPreflight
import com.baatiku.omnilive.stream.LiveSessionPhase
import com.baatiku.omnilive.stream.LiveSessionState
import com.baatiku.omnilive.stream.LiveStreamService
import com.baatiku.omnilive.stream.LiveTelemetry
import com.baatiku.omnilive.stream.ReferenceImageStore
import com.baatiku.omnilive.ui.CameraPreviewView
import com.baatiku.omnilive.ui.ReturnedVideoView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val OmniBackground=Color(0xFF090D12)
private val OmniSurface=Color(0xFF111820)
private val OmniSurfaceRaised=Color(0xFF18212B)
private val OmniAccent=Color(0xFF6EE7D8)
private val OmniOnAccent=Color(0xFF06201D)
private val OmniText=Color(0xFFF3F7F8)
private val OmniMuted=Color(0xFF9AA8B4)
private val OmniDanger=Color(0xFFFF7A7A)

class MainActivity:ComponentActivity() {
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme=darkColorScheme(
                    primary=OmniAccent,
                    onPrimary=OmniOnAccent,
                    background=OmniBackground,
                    onBackground=OmniText,
                    surface=OmniSurface,
                    onSurface=OmniText,
                    surfaceVariant=OmniSurfaceRaised,
                    onSurfaceVariant=OmniMuted,
                    error=OmniDanger
                )
            ) { OmniLiveApp() }
        }
    }
}

@Composable
private fun OmniLiveApp() {
    val context=LocalContext.current
    val prefs=remember { context.getSharedPreferences("omnilive",android.content.Context.MODE_PRIVATE) }
    val scope=rememberCoroutineScope()
    val liveSession by LiveSessionState.state.collectAsStateWithLifecycle()
    val telemetry by LiveTelemetry.state.collectAsStateWithLifecycle()
    val running=liveSession.isRunning

    var page by remember { mutableIntStateOf(0) }
    var profile by remember { mutableStateOf(prefs.getString("profile","balanced") ?: "balanced") }
    var backendUrl by remember {
        val saved = prefs.getString("backend_url", BuildConfig.DEFAULT_BACKEND_URL) ?: BuildConfig.DEFAULT_BACKEND_URL
        val resolved = if (saved == "http://10.0.2.2:8000" || saved.isBlank()) BuildConfig.DEFAULT_BACKEND_URL else saved
        if (resolved != saved) prefs.edit().putString("backend_url", resolved).apply()
        mutableStateOf(resolved)
    }
    var referencePath by remember { mutableStateOf(prefs.getString("reference_image_path",null)) }
    var referencePreview by remember { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    LaunchedEffect(referencePath) {
        referencePreview=withContext(Dispatchers.IO) {
            ReferenceImageStore.preview(referencePath)?.asImageBitmap()
        }
    }
    var referenceStatus by remember { mutableStateOf<String?>(null) }
    var backendChecking by remember { mutableStateOf(false) }
    var backendCheckStatus by remember { mutableStateOf<String?>(null) }
    var advancedOpen by remember { mutableStateOf(false) }
    var copiedStatus by remember { mutableStateOf<String?>(null) }
    var cameraGranted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context,Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED)
    }

    val permissionLauncher=rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        cameraGranted=it
    }
    val notificationPermissionLauncher=rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if(!cameraGranted) permissionLauncher.launch(Manifest.permission.CAMERA)
        if(android.os.Build.VERSION.SDK_INT>=33 &&
            ContextCompat.checkSelfPermission(context,Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    val referencePicker=rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if(uri!=null) scope.launch {
            referenceStatus="Preparing photo…"
            runCatching {
                withContext(Dispatchers.IO) { ReferenceImageStore.importImage(context,uri) }
            }.onSuccess { imported ->
                referencePath=imported.path
                prefs.edit().putString("reference_image_path",imported.path).apply()
                referencePreview=withContext(Dispatchers.IO) {
                    ReferenceImageStore.preview(imported.path)?.asImageBitmap()
                }
                referenceStatus="Ready to use (${imported.bytes/1024} KB)"
                LiveTelemetry.update {
                    it.copy(
                        referenceImageStatus="selected",
                        referenceImageWidth=imported.width,
                        referenceImageHeight=imported.height,
                        referenceImageError=null
                    )
                }
            }.onFailure {
                referenceStatus=it.message ?: "Could not prepare this photo"
            }
        }
    }

    fun startSession() {
        if(!cameraGranted) {
            permissionLauncher.launch(Manifest.permission.CAMERA)
            return
        }
        val intent=Intent(context,LiveStreamService::class.java)
            .putExtra("profile",profile)
            .putExtra("effect","face_swap")
            .putExtra("backend_url",backendUrl)
        referencePath?.let { intent.putExtra("reference_image_path",it) }
        runCatching {
            LiveSessionState.starting(profile,"face_swap",backendUrl)
            ContextCompat.startForegroundService(context,intent)
        }.onFailure { LiveSessionState.failed(it) }
    }

    fun stopSession() {
        LiveSessionState.stopping()
        context.stopService(Intent(context,LiveStreamService::class.java))
    }

    Scaffold(
        containerColor=OmniBackground,
        bottomBar={
            NavigationBar(containerColor=OmniSurface) {
                NavigationBarItem(selected=page==0,onClick={page=0},icon={Text("●")},label={Text("Camera")})
                NavigationBarItem(selected=page==1,onClick={page=1},icon={Text("✦")},label={Text("Face")})
                NavigationBarItem(selected=page==2,onClick={page=2},icon={Text("⚙")},label={Text("Settings")})
            }
        }
    ) { innerPadding ->
        when(page) {
            0 -> CameraHome(
                modifier=Modifier.padding(innerPadding),
                running=running,
                phase=liveSession.phase,
                connectionState=telemetry.connectionState,
                hasProcessedVideo=(telemetry.framesReceived ?: 0L)>0L,
                error=liveSession.error ?: telemetry.lastError,
                cameraGranted=cameraGranted,
                referenceSelected=referencePath!=null,
                onStart={startSession()},
                onStop={stopSession()},
                onGrantCamera={permissionLauncher.launch(Manifest.permission.CAMERA)},
                onOpenLook={page=1}
            )
            1 -> LookScreen(
                modifier=Modifier.padding(innerPadding),
                running=running,
                referencePreview=referencePreview,
                referenceSelected=referencePath!=null,
                referenceStatus=referenceStatus,
                onPickReference={referencePicker.launch("image/*")},
                onRemoveReference={
                    ReferenceImageStore.clear(referencePath)
                    referencePath=null
                    referencePreview=null
                    referenceStatus=null
                    prefs.edit().remove("reference_image_path").apply()
                    LiveTelemetry.update {
                        it.copy(
                            referenceImageStatus="none",
                            referenceImageWidth=null,
                            referenceImageHeight=null,
                            referenceImageError=null
                        )
                    }
                }
            )
            else -> SettingsScreen(
                modifier=Modifier.padding(innerPadding),
                running=running,
                profile=profile,
                backendUrl=backendUrl,
                advancedOpen=advancedOpen,
                backendChecking=backendChecking,
                backendCheckStatus=backendCheckStatus,
                copiedStatus=copiedStatus,
                onProfileChange={
                    profile=it
                    prefs.edit().putString("profile",it).apply()
                },
                onBackendChange={
                    backendUrl=it
                    prefs.edit().putString("backend_url",it).apply()
                },
                onToggleAdvanced={advancedOpen=!advancedOpen},
                onTestTransport={
                    if(!backendChecking) {
                        backendChecking=true
                        backendCheckStatus="Checking connection…"
                        scope.launch {
                            val result=runCatching {
                                withContext(Dispatchers.IO) {
                                    TransportPreflight.run(backendUrl,profile,"face_swap")
                                }
                            }
                            backendCheckStatus=result.fold(
                                onSuccess={it.summary()},
                                onFailure={"Connection test failed: "+(it.message ?: it.javaClass.simpleName)}
                            )
                            backendChecking=false
                        }
                    }
                },
                onCopyDiagnostics={
                    val device=DeviceDiagnostics.snapshot(context)
                    val rootLab=RootLabDiagnosticsReader.read()
                    context.getSystemService(ClipboardManager::class.java).setPrimaryClip(
                        ClipData.newPlainText(
                            "OmniLive diagnostics",
                            buildString {
                                appendLine(device.asText(backendUrl,referencePath!=null))
                                rootLab?.let {
                                    appendLine("Root lab: "+it.summary)
                                    if(it.json.isNotBlank()) appendLine(it.json)
                                }
                                appendLine()
                                append(LiveDiagnostics.asText(telemetry))
                            }
                        )
                    )
                    copiedStatus="Diagnostics copied"
                }
            )
        }
    }
}

@Composable
private fun CameraHome(
    modifier:Modifier,
    running:Boolean,
    phase:LiveSessionPhase,
    connectionState:String,
    hasProcessedVideo:Boolean,
    error:String?,
    cameraGranted:Boolean,
    referenceSelected:Boolean,
    onStart:()->Unit,
    onStop:()->Unit,
    onGrantCamera:()->Unit,
    onOpenLook:()->Unit
) {
    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),
        verticalArrangement=Arrangement.spacedBy(18.dp)
    ) {
        Column(verticalArrangement=Arrangement.spacedBy(3.dp)) {
            Text("OmniLive",style=MaterialTheme.typography.headlineMedium,fontWeight=FontWeight.Medium)
            Text(if(running) "Your camera is active" else "Live camera, made simple",color=OmniMuted)
        }

        Card(
            modifier=Modifier.fillMaxWidth(),
            shape=RoundedCornerShape(26.dp),
            colors=CardDefaults.cardColors(containerColor=Color.Black)
        ) {
            Box(
                Modifier.fillMaxWidth().aspectRatio(9f/14f).background(Color.Black),
                contentAlignment=Alignment.Center
            ) {
                if(running) {
                    if(hasProcessedVideo) ReturnedVideoView(Modifier.fillMaxSize())
                    else CameraPreviewView(Modifier.fillMaxSize())

                    Surface(
                        modifier=Modifier.align(Alignment.TopStart).padding(14.dp),
                        shape=RoundedCornerShape(100.dp),
                        color=Color.Black.copy(alpha=0.64f)
                    ) {
                        Text(
                            when {
                                hasProcessedVideo -> "LIVE · processed"
                                connectionState=="failed" -> "Connection problem"
                                phase==LiveSessionPhase.STARTING -> "Starting camera…"
                                else -> "LIVE · camera"
                            },
                            modifier=Modifier.padding(horizontal=12.dp,vertical=7.dp),
                            color=if(error==null) OmniText else OmniDanger,
                            style=MaterialTheme.typography.labelMedium
                        )
                    }

                    if(!hasProcessedVideo) {
                        Surface(
                            modifier=Modifier.align(Alignment.BottomCenter).padding(14.dp),
                            shape=RoundedCornerShape(16.dp),
                            color=Color.Black.copy(alpha=0.68f)
                        ) {
                            Text(
                                if(connectionState=="connected") "Waiting for the processed view…"
                                else "Camera is ready. Connecting the processed view…",
                                modifier=Modifier.padding(horizontal=14.dp,vertical=10.dp),
                                color=OmniText,
                                style=MaterialTheme.typography.bodySmall,
                                textAlign=TextAlign.Center
                            )
                        }
                    }
                } else {
                    Column(
                        horizontalAlignment=Alignment.CenterHorizontally,
                        verticalArrangement=Arrangement.spacedBy(10.dp),
                        modifier=Modifier.padding(28.dp)
                    ) {
                        Text("◉",style=MaterialTheme.typography.displaySmall,color=OmniAccent)
                        Text(
                            "Your camera preview appears here",
                            color=OmniText,
                            style=MaterialTheme.typography.titleMedium,
                            fontWeight=FontWeight.Medium,
                            textAlign=TextAlign.Center
                        )
                        Text(
                            "Tap Start camera below. You will keep seeing yourself while OmniLive connects.",
                            color=OmniMuted,
                            textAlign=TextAlign.Center
                        )
                    }
                }
            }
        }

        error?.let {
            Card(
                colors=CardDefaults.cardColors(containerColor=OmniDanger.copy(alpha=0.12f)),
                shape=RoundedCornerShape(18.dp)
            ) {
                Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(5.dp)) {
                    Text("Something needs attention",color=OmniDanger,fontWeight=FontWeight.Medium)
                    Text(it,color=OmniText,style=MaterialTheme.typography.bodySmall)
                }
            }
        }

        if(!cameraGranted) {
            Card(colors=CardDefaults.cardColors(containerColor=OmniSurfaceRaised),shape=RoundedCornerShape(18.dp)) {
                Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
                    Text("Camera access is off",fontWeight=FontWeight.Medium)
                    Text("OmniLive needs camera permission before a live view can start.",color=OmniMuted)
                    OutlinedButton(onClick=onGrantCamera) { Text("Allow camera") }
                }
            }
        }

        Button(
            onClick={if(running) onStop() else onStart()},
            enabled=running || cameraGranted,
            modifier=Modifier.fillMaxWidth().height(56.dp),
            shape=RoundedCornerShape(18.dp)
        ) {
            Text(
                when {
                    phase==LiveSessionPhase.STOPPING -> "Stopping…"
                    running -> "Stop camera"
                    else -> "Start camera"
                },
                fontWeight=FontWeight.Medium
            )
        }

        QuickInfoCard(Modifier.fillMaxWidth(),"Face source",if(referenceSelected) "Ready" else "None")

        FilledTonalButton(
            onClick=onOpenLook,
            modifier=Modifier.fillMaxWidth(),
            shape=RoundedCornerShape(16.dp)
        ) { Text(if(referenceSelected) "Change face photo" else "Set up face swap") }
    }
}

@Composable
private fun QuickInfoCard(modifier:Modifier,title:String,value:String) {
    Card(modifier=modifier,colors=CardDefaults.cardColors(containerColor=OmniSurface),shape=RoundedCornerShape(18.dp)) {
        Column(Modifier.padding(15.dp),verticalArrangement=Arrangement.spacedBy(4.dp)) {
            Text(title,color=OmniMuted,style=MaterialTheme.typography.labelMedium)
            Text(value,color=OmniText,fontWeight=FontWeight.Medium)
        }
    }
}

@Composable
private fun LookScreen(
    modifier:Modifier,
    running:Boolean,
    referencePreview:androidx.compose.ui.graphics.ImageBitmap?,
    referenceSelected:Boolean,
    referenceStatus:String?,
    onPickReference:()->Unit,
    onRemoveReference:()->Unit
) {
    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),
        verticalArrangement=Arrangement.spacedBy(18.dp)
    ) {
        Text("Face swap",style=MaterialTheme.typography.headlineMedium,fontWeight=FontWeight.Medium)
        Text("Choose the face photo OmniLive should swap onto you. Face swap is always on — there is nothing else to set up.",color=OmniMuted)

        Card(colors=CardDefaults.cardColors(containerColor=OmniSurface),shape=RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(14.dp)) {
                Text("Reference photo",style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Medium)
                if(referencePreview!=null) {
                    Image(
                        bitmap=referencePreview,
                        contentDescription="Reference photo",
                        contentScale=ContentScale.Crop,
                        modifier=Modifier.fillMaxWidth().aspectRatio(4f/3f).clip(RoundedCornerShape(18.dp))
                    )
                } else {
                    Box(
                        Modifier.fillMaxWidth().aspectRatio(4f/3f).clip(RoundedCornerShape(18.dp)).background(OmniSurfaceRaised),
                        contentAlignment=Alignment.Center
                    ) { Text("No photo selected",color=OmniMuted) }
                }

                Button(onClick=onPickReference,enabled=!running,modifier=Modifier.fillMaxWidth()) {
                    Text(if(referenceSelected) "Choose another photo" else "Choose a photo")
                }
                if(referenceSelected) {
                    TextButton(onClick=onRemoveReference,enabled=!running,modifier=Modifier.fillMaxWidth()) {
                        Text("Remove photo")
                    }
                }
                referenceStatus?.let { Text(it,color=OmniMuted,style=MaterialTheme.typography.bodySmall) }
                if(running) Text("Stop the camera before changing the reference photo.",color=OmniMuted,style=MaterialTheme.typography.bodySmall)
            }
        }

        Card(colors=CardDefaults.cardColors(containerColor=OmniSurface),shape=RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Text("How it works",style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Medium)
                Text(
                    "Your camera streams to the face-swap server and the swapped video comes back to your screen. The photo above is the face that gets swapped onto you.",
                    color=OmniMuted,
                    style=MaterialTheme.typography.bodySmall
                )
            }
        }
    }
}

@Composable
private fun SettingsScreen(
    modifier:Modifier,
    running:Boolean,
    profile:String,
    backendUrl:String,
    advancedOpen:Boolean,
    backendChecking:Boolean,
    backendCheckStatus:String?,
    copiedStatus:String?,
    onProfileChange:(String)->Unit,
    onBackendChange:(String)->Unit,
    onToggleAdvanced:()->Unit,
    onTestTransport:()->Unit,
    onCopyDiagnostics:()->Unit
) {
    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),
        verticalArrangement=Arrangement.spacedBy(18.dp)
    ) {
        Text("Settings",style=MaterialTheme.typography.headlineMedium,fontWeight=FontWeight.Medium)

        Card(colors=CardDefaults.cardColors(containerColor=OmniSurface),shape=RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Text("Video quality",style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Medium)
                Text("Balanced is recommended. OmniLive can still adapt automatically during a session.",color=OmniMuted)
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    listOf("low_latency" to "Fast","balanced" to "Balanced","high" to "High").forEach {
                        FilterChip(
                            selected=profile==it.first,
                            onClick={onProfileChange(it.first)},
                            enabled=!running,
                            label={Text(it.second)}
                        )
                    }
                }
            }
        }

        OutlinedButton(onClick=onToggleAdvanced,modifier=Modifier.fillMaxWidth(),shape=RoundedCornerShape(16.dp)) {
            Text(if(advancedOpen) "Hide advanced settings" else "Advanced settings")
        }

        if(advancedOpen) {
            Card(colors=CardDefaults.cardColors(containerColor=OmniSurface),shape=RoundedCornerShape(24.dp)) {
                Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                    Text("Developer tools",style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Medium)
                    Text("Backend and device debugging lives here. Normal use does not require these controls.",color=OmniMuted,style=MaterialTheme.typography.bodySmall)
                    OutlinedTextField(
                        value=backendUrl,
                        onValueChange=onBackendChange,
                        enabled=!running,
                        singleLine=true,
                        label={Text("Backend URL")},
                        modifier=Modifier.fillMaxWidth()
                    )
                    Button(onClick=onTestTransport,enabled=!backendChecking && backendUrl.isNotBlank(),modifier=Modifier.fillMaxWidth()) {
                        Text(if(backendChecking) "Checking…" else "Test connection")
                    }
                    backendCheckStatus?.let { Text(it,color=if(it.startsWith("Transport OK")) OmniAccent else OmniMuted,style=MaterialTheme.typography.bodySmall) }
                    HorizontalDivider()
                    OutlinedButton(onClick=onCopyDiagnostics,modifier=Modifier.fillMaxWidth()) { Text("Copy diagnostics") }
                    copiedStatus?.let { Text(it,color=OmniMuted,style=MaterialTheme.typography.bodySmall) }
                    Text(
                        "App "+BuildConfig.VERSION_NAME+" · build "+BuildConfig.BUILD_COMMIT.take(12)+if(BuildConfig.ROOT_LAB_ENABLED) " · root-lab" else "",
                        color=OmniMuted,
                        style=MaterialTheme.typography.labelSmall
                    )
                }
            }
        }
    }
}
