package com.genai.debugger.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.Base64
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import com.genai.debugger.network.WebSocketStreamClient
import kotlinx.coroutines.*
import java.io.ByteArrayOutputStream

/**
 * Foreground Service capturing screen frames via MediaProjection and streaming over WebSockets.
 *
 * Conforms to Android 14 FOREGROUND_SERVICE_MEDIA_PROJECTION requirements.
 *
 * Author: Arun Krishnan
 */
class MediaProjectionService : Service() {

    companion object {
        const val TAG = "MediaProjectionService"
        const val ACTION_START = "com.genai.debugger.START_CAPTURE"
        const val ACTION_STOP = "com.genai.debugger.STOP_CAPTURE"
        const val EXTRA_RESULT_CODE = "extra_result_code"
        const val EXTRA_RESULT_DATA = "extra_result_data"
        const val EXTRA_SERVER_HOST = "extra_server_host"
        const val EXTRA_SERVER_PORT = "extra_server_port"
        private const val NOTIFICATION_CHANNEL_ID = "genai_debugger_stream"
        private const val NOTIFICATION_ID = 1001
    }

    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private val serviceScope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private lateinit var webSocketClient: WebSocketStreamClient

    private var targetFps = 10
    private var isStreaming = false
    private var lastFrameTime = 0L

    override fun onCreate() {
        super.onCreate()
        webSocketClient = WebSocketStreamClient()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
                val resultData = intent.getParcelableExtra<Intent>(EXTRA_RESULT_DATA)
                val host = intent.getStringExtra(EXTRA_SERVER_HOST) ?: "10.0.2.2"
                val port = intent.getIntExtra(EXTRA_SERVER_PORT, 8000)

                startForeground(NOTIFICATION_ID, buildNotification())

                if (resultData != null && !isStreaming) {
                    val wsUrl = "ws://$host:$port/ws/stream"
                    webSocketClient.connect(wsUrl) { command, payload ->
                        handleDownstreamCommand(command, payload)
                    }
                    startScreenCapture(resultCode, resultData)
                }
            }
            ACTION_STOP -> {
                stopScreenCapture()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    /**
     * Configures VirtualDisplay, ImageReader buffer, and initiates frame capture loop.
     *
     * @param resultCode Consent activity result code from MediaProjectionManager.
     * @param resultData Intent payload containing MediaProjection grant token.
     */
    private fun startScreenCapture(resultCode: Int, resultData: Intent) {
        val mpManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = mpManager.getMediaProjection(resultCode, resultData)

        val windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        windowManager.defaultDisplay.getRealMetrics(metrics)

        // Downsample to max width 720p to preserve bandwidth and latency
        val scale = if (metrics.widthPixels > 720) 720f / metrics.widthPixels else 1.0f
        val width = (metrics.widthPixels * scale).toInt()
        val height = (metrics.heightPixels * scale).toInt()
        val density = metrics.densityDpi

        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "GenAIDebuggerDisplay",
            width,
            height,
            density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface,
            null,
            null
        )

        isStreaming = true
        imageReader?.setOnImageAvailableListener({ reader ->
            val now = System.currentTimeMillis()
            val frameIntervalMs = 1000 / targetFps
            if (now - lastFrameTime < frameIntervalMs) {
                // Drop frame to throttle FPS
                val dropped = reader.acquireLatestImage()
                dropped?.close()
                return@setOnImageAvailableListener
            }
            lastFrameTime = now

            val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
            serviceScope.launch {
                try {
                    val planes = image.planes
                    val buffer = planes[0].buffer
                    // Account for GPU row padding alignment before copying pixel buffer to prevent skewed bitmaps
                    val pixelStride = planes[0].pixelStride
                    val rowStride = planes[0].rowStride
                    val rowPadding = rowStride - pixelStride * width

                    val bitmap = Bitmap.createBitmap(
                        width + rowPadding / pixelStride,
                        height,
                        Bitmap.Config.ARGB_8888
                    )
                    bitmap.copyPixelsFromBuffer(buffer)
                    val croppedBitmap = Bitmap.createBitmap(bitmap, 0, 0, width, height)

                    val outputStream = ByteArrayOutputStream()
                    croppedBitmap.compress(Bitmap.CompressFormat.JPEG, 75, outputStream)
                    val bytes = outputStream.toByteArray()
                    val base64 = Base64.encodeToString(bytes, Base64.NO_WRAP)

                    webSocketClient.sendFrame(
                        imageBase64 = base64,
                        width = width,
                        height = height,
                        mimeType = "image/jpeg"
                    )

                    bitmap.recycle()
                    croppedBitmap.recycle()
                } catch (e: Exception) {
                    Log.e(TAG, "Error encoding frame: ${e.message}")
                } finally {
                    image.close()
                }
            }
        }, null)
    }

    /**
     * Handles downstream device control commands received via the WebSocket session.
     */
    private fun handleDownstreamCommand(command: String, payload: Map<String, Any>) {
        when (command) {
            "SET_FPS" -> {
                val newFps = (payload["fps"] as? Number)?.toInt() ?: 10
                targetFps = newFps.coerceIn(1, 30)
                Log.i(TAG, "Updated streaming FPS to $targetFps")
            }
            "REQUEST_KEYFRAME" -> {
                lastFrameTime = 0L // Force next frame
            }
        }
    }

    /**
     * Releases VirtualDisplay, ImageReader, MediaProjection token, and cancels active coroutine scope.
     */
    private fun stopScreenCapture() {
        isStreaming = false
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        mediaProjection?.stop()
        mediaProjection = null
        webSocketClient.disconnect()
        serviceScope.cancel()
    }

    /**
     * Registers the Android O+ notification channel for foreground service execution.
     */
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "Screen Streaming Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Active MediaProjection streaming session"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    /**
     * Builds the persistent notification required for Android 14 MediaProjection foreground service.
     */
    private fun buildNotification(): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }
        return builder
            .setContentTitle("GenAI Mobile Debugger")
            .setContentText("Screen streaming is actively running")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()
    }

    override fun onDestroy() {
        stopScreenCapture()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
