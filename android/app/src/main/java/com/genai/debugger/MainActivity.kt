package com.genai.debugger

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.genai.debugger.service.MediaProjectionService

/**
 * Main Activity managing MediaProjection capture consent and service lifecycle.
 *
 * Author: Arun Krishnan
 */
class MainActivity : ComponentActivity() {

    private lateinit var mediaProjectionManager: MediaProjectionManager
    private var isStreamingState = mutableStateOf(false)
    private var serverHostState = mutableStateOf("10.0.2.2")
    private var serverPortState = mutableStateOf("8000")

    private val captureLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            startStreamingService(result.resultCode, result.data!!)
            isStreamingState.value = true
        } else {
            Toast.makeText(this, "Screen capture permission denied", Toast.LENGTH_SHORT).show()
            isStreamingState.value = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager

        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    DebuggerControlScreen(
                        isStreaming = isStreamingState.value,
                        serverHost = serverHostState.value,
                        serverPort = serverPortState.value,
                        onHostChange = { serverHostState.value = it },
                        onPortChange = { serverPortState.value = it },
                        onToggleStreaming = { toggleStreaming() }
                    )
                }
            }
        }
    }

    /**
     * Toggles screen streaming state, requesting MediaProjection user consent if idle.
     */
    private fun toggleStreaming() {
        if (isStreamingState.value) {
            stopStreamingService()
            isStreamingState.value = false
        } else {
            val captureIntent = mediaProjectionManager.createScreenCaptureIntent()
            captureLauncher.launch(captureIntent)
        }
    }

    /**
     * Starts the foreground MediaProjection capture service with permission token and network config.
     */
    private fun startStreamingService(resultCode: Int, data: Intent) {
        val serviceIntent = Intent(this, MediaProjectionService::class.java).apply {
            action = MediaProjectionService.ACTION_START
            putExtra(MediaProjectionService.EXTRA_RESULT_CODE, resultCode)
            putExtra(MediaProjectionService.EXTRA_RESULT_DATA, data)
            putExtra(MediaProjectionService.EXTRA_SERVER_HOST, serverHostState.value)
            putExtra(MediaProjectionService.EXTRA_SERVER_PORT, serverPortState.value.toIntOrNull() ?: 8000)
        }
        startForegroundService(serviceIntent)
    }

    /**
     * Sends STOP action to the MediaProjection foreground service.
     */
    private fun stopStreamingService() {
        val serviceIntent = Intent(this, MediaProjectionService::class.java).apply {
            action = MediaProjectionService.ACTION_STOP
        }
        startService(serviceIntent)
    }
}

/**
 * Developer control screen providing server connection configuration and streaming toggle controls.
 */
@Composable
fun DebuggerControlScreen(
    isStreaming: Boolean,
    serverHost: String,
    serverPort: String,
    onHostChange: (String) -> Unit,
    onPortChange: (String) -> Unit,
    onToggleStreaming: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = "GenAI Debugger Client",
            style = MaterialTheme.typography.headlineMedium
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = "High-performance two-way screen & diagnostics stream",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Spacer(modifier = Modifier.height(32.dp))

        OutlinedTextField(
            value = serverHost,
            onValueChange = onHostChange,
            label = { Text("Server Host (e.g. 10.0.2.2 or LAN IP)") },
            modifier = Modifier.fillMaxWidth(),
            enabled = !isStreaming
        )

        Spacer(modifier = Modifier.height(16.dp))

        OutlinedTextField(
            value = serverPort,
            onValueChange = onPortChange,
            label = { Text("Server Port") },
            modifier = Modifier.fillMaxWidth(),
            enabled = !isStreaming
        )

        Spacer(modifier = Modifier.height(32.dp))

        Button(
            onClick = onToggleStreaming,
            colors = ButtonDefaults.buttonColors(
                containerColor = if (isStreaming) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
            ),
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
        ) {
            Text(text = if (isStreaming) "Stop Streaming" else "Start Live Screen Stream")
        }

        Spacer(modifier = Modifier.height(16.dp))

        Text(
            text = if (isStreaming) "Status: STREAMING ACTIVE" else "Status: IDLE",
            style = MaterialTheme.typography.labelLarge,
            color = if (isStreaming) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline
        )
    }
}
