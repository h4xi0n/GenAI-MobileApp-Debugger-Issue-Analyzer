package com.genai.debugger.network

import android.util.Log
import okhttp3.*
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Resilient OkHttp WebSocket client for high-throughput mobile frame streaming and two-way control.
 *
 * Author: Arun Krishnan
 */
class WebSocketStreamClient {

    companion object {
        private const val TAG = "WebSocketStreamClient"
    }

    private val client: OkHttpClient = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var isConnected = false
    private var commandListener: ((String, Map<String, Any>) -> Unit)? = null

    /**
     * Connects to the backend WebSocket stream endpoint.
     *
     * @param url WebSocket URL (e.g. ws://10.0.2.2:8000/ws/stream).
     * @param onCommandReceived Callback invoked when downstream control messages arrive.
     */
    fun connect(url: String, onCommandReceived: (String, Map<String, Any>) -> Unit) {
        this.commandListener = onCommandReceived
        val request = Request.Builder().url(url).build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                Log.i(TAG, "Connected to WebSocket stream server: $url")
                isConnected = true
            }

            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    val json = JSONObject(text)
                    val type = json.optString("type", "")
                    val payloadObj = json.optJSONObject("payload")
                    val payload = mutableMapOf<String, Any>()
                    if (payloadObj != null) {
                        for (key in payloadObj.keys()) {
                            payload[key] = payloadObj.get(key)
                        }
                    }
                    commandListener?.invoke(type, payload)
                } catch (e: Exception) {
                    Log.e(TAG, "Error parsing incoming control message: ${e.message}")
                }
            }

            override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WebSocket closing: $reason (code: $code)")
                isConnected = false
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "WebSocket connection failure: ${t.message}")
                isConnected = false
            }
        })
    }

    /**
     * Enqueues and transmits a Base64-encoded screen frame over the active WebSocket channel.
     *
     * @param imageBase64 Raw Base64 string of the compressed frame bitmap.
     * @param width Frame pixel width.
     * @param height Frame pixel height.
     * @param mimeType Frame image MIME format ('image/jpeg' or 'image/webp').
     */
    fun sendFrame(imageBase64: String, width: Int, height: Int, mimeType: String = "image/jpeg") {
        if (!isConnected || webSocket == null) return

        try {
            val framePayload = JSONObject().apply {
                put("image_bytes_base64", imageBase64)
                put("width", width)
                put("height", height)
                put("mime_type", mimeType)
                put("screen_state", "NORMAL")
            }

            val envelope = JSONObject().apply {
                put("type", "frame")
                put("payload", framePayload)
            }

            webSocket?.send(envelope.toString())
        } catch (e: Exception) {
            Log.e(TAG, "Failed to send frame via WebSocket: ${e.message}")
        }
    }

    /**
     * Gracefully closes the active WebSocket connection and releases socket resources.
     */
    fun disconnect() {
        isConnected = false
        webSocket?.close(1000, "Service stopped")
        webSocket = null
    }
}
