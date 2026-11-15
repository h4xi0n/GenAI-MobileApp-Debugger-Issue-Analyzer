# Architectural Foundations Guide

## GenAI Mobile App Debugger & Issue Analyzer

**Author:** Arun Krishnan  
**Platform Scope:** Android (Kotlin, Jetpack Compose, MediaProjection) + Python (FastAPI, WebSockets, Pydantic v2, Pillow) + AI Developer Tooling (Cursor Composer, Antigravity MAS).

---

## 1. System Overview & Architectural Blueprint

The **GenAI Mobile App Debugger & Issue Analyzer** is an end-to-end platform bridging runtime mobile device anomalies (crashes, ANRs, visual glitches, frozen state) directly to automated developer remediation.

Mobile debugging fundamentally relies on two complementary data channels:
1. **Visual UI Telemetry:** Live frame captures of what is rendered on screen (crash dialogs, ANR dialogs, empty states, frozen compositions).
2. **Runtime Log Telemetry:** High-throughput `logcat` streams containing system events, lifecycle transitions, warning markers, and exception stack traces.

The platform continuously streams and buffers both sensory streams, scrubs sensitive information via a compiled PII redaction engine, correlates anomalous screen states with logcat stack traces, and synthesizes actionable remediation diffs for **Cursor Composer** and **Antigravity** specialist subagents.

```mermaid
flowchart TD
    subgraph MobileDevice["Android Target Layer"]
        ScreenCapture["Screen Surface / MediaProjection"]
        LogcatPipe["Logcat Stream (/dev/log/main)"]
    end

    subgraph CoreIngestion["Core Processing Layer (src/core)"]
        ADB["ADBManager\n(Device Discovery & Shell)"]
        ScreenInsp["ScreenInspector\n(Lanczos Resize, WebP/JPEG)"]
        RingBuf["FrameRingBuffer\n(Rolling 5-Frame Window)"]
        Parser["LogcatParser\n(Threadtime Parsing & Stack Extraction)"]
        Sanitizer["PIISanitizer\n(Regex Redaction: JWT, Tokens, IP)"]
        LogWindow["SlidingLogWindow\n(Token-Bounded Buffer)"]
        PatchGen["PatchGenerator\n(Unified Git Diff Engine)"]
    end

    subgraph WebBackend["Two-Way Web Server (apps/server.py)"]
        WS_Stream["/ws/stream (Frame Broadcast)"]
        WS_Logcat["/ws/logcat (Live Log Stream)"]
        WS_Control["/ws/control (Full-Duplex Commands)"]
        REST_API["REST Endpoints (/api/diagnose, /api/patch)"]
    end

    subgraph FrontendUI["Web UI Dashboard (apps/static & templates)"]
        CanvasView["Live Device Viewport (Canvas + FPS)"]
        LogcatView["Interactive Logcat Terminal"]
        DiagView["Multimodal GenAI Triage"]
        PatchView["IDE Patch Bridge (Cursor & Antigravity)"]
        ControlView["Two-Way Device Controls"]
    end

    ScreenCapture --> WS_Stream
    LogcatPipe --> ADB --> Parser --> Sanitizer --> LogWindow
    ADB --> ScreenInsp --> RingBuf

    RingBuf --> WS_Stream --> CanvasView
    LogWindow --> WS_Logcat --> LogcatView
    LogWindow & RingBuf --> REST_API --> DiagView
    REST_API --> PatchGen --> PatchView
    ControlView --> WS_Control --> ADB
```

---

## 2. In-Depth Component Breakdown

### 2.1 Data Contracts & Domain Models (`src/core/models.py`)

Every layer communicates through strict, validated **Pydantic v2** models, eliminating implicit dictionary parsing and ensuring runtime type safety.

- **`LogLevel`**: Android severity enum (`VERBOSE`, `DEBUG`, `INFO`, `WARN`, `ERROR`, `FATAL`).
- **`LogEntry`**: Represents a single threadtime log line. Trims tags, isolates exception traces, and sanitizes payloads.
- **`ScreenState`**: Enum tracking visual anomalies (`NORMAL`, `CRASH_DIALOG`, `ANR_DIALOG`, `BLANK_SCREEN`, `FROZEN_UI`, `LAYOUT_GLITCH`).
- **`ScreenFrame`**: Encapsulates compressed image base64, mime type (`image/webp` or `image/jpeg`), resolution, frame UUID, and timestamp.
- **`CrashSignature`**: Structured crash metadata isolating exception type (`java.lang.NullPointerException`), package name, offending file, line number, and raw stack trace.
- **`DiagnosticReport`**: Multimodal diagnosis contract with confidence rating (0.0–1.0), root cause analysis, affected components, and remediation suggestions.
- **`IDEActionPrompt`**: Remediation output formatted for target IDEs (`cursor` or `antigravity`), complete with target files and unified diffs.
- **`DeviceCommand` & `DeviceStatus`**: Control protocol messages managing two-way communication.

---

### 2.2 ADB Manager & Subprocess Engine (`src/core/adb_manager.py`)

The ADB Manager orchestrates physical devices and emulators over USB or TCP.

- **Device Discovery (`list_devices`)**: Parses `adb devices -l` to track serials, model names, and connection states.
- **Subprocess Logcat Streaming (`stream_logcat_lines`)**: Spawns an asynchronous generator reading `adb logcat -v threadtime` with non-blocking readline and graceful shutdown.
- **Screen Capture (`capture_screenshot`)**: Runs `adb exec-out screencap -p` returning raw PNG bytes.
- **Lifecycle & Inspection**: Provides package launch (`am start`), force-stop (`am force-stop`), data wipe (`pm clear`), and port reversal (`adb reverse tcp:8000 tcp:8000`).
- **Offline Mock Fallback**: When no device is attached or in CI environments, `ADBManager` operates in mock mode, producing synthetic 720x1280 screen frames and realistic logcat traces.

---

### 2.3 Logcat Parser, Exception Isolator & PII Sanitizer (`src/core/logcat_parser.py`)

- **`LogcatParser`**: Uses regex `^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+([^:]+):\s*(.*)$` to parse threadtime logs into structured `LogEntry` records. Detects continuation stack lines (`\tat `, `Caused by:`, `AndroidRuntime`).
- **`PIISanitizer`**: Pre-filtering security gateway executing compiled regex replacements before logs reach the Web UI or LLM prompts:
  - Authorization / Bearer tokens: `(?i)bearer\s+[a-zA-Z0-9_\-\.]+` $\rightarrow$ `Bearer [REDACTED_TOKEN]`
  - JWT strings: `eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+` $\rightarrow$ `[REDACTED_JWT]`
  - Email addresses: `[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+` $\rightarrow$ `[REDACTED_EMAIL]`
  - UUIDs: `\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b` $\rightarrow$ `[REDACTED_UUID]`
  - Non-loopback IPv4 addresses $\rightarrow$ `[REDACTED_IP]`
- **`SlidingLogWindow`**: Bounded in-memory FIFO buffer retaining up to 300 lines with an approximate token ceiling (4,000 tokens), allowing level-filtered retrieval.

---

### 2.4 Screen Inspector & Frame Ring Buffer (`src/core/screen_inspector.py`)

Screen captures can be large ($1080 \times 2400+$ at ~5 MB uncompressed PNG), which rapidly consumes bandwidth and LLM token budgets.

- **Lanczos Downsampling**: Scales the image down proportionally so that neither width nor height exceeds 1024px.
- **Compression**: Re-encodes frames to WebP (quality 80) or JPEG, reducing typical payload size from 5 MB to ~80–120 KB.
- **`FrameRingBuffer`**: Thread-safe rolling ring buffer holding the most recent 5 frames. When a crash or manual trigger occurs, the ring buffer yields a deterministic multi-frame sequence leading up to the failure.

---

### 2.5 IDE Patch & Action Prompt Bridge (`src/core/patch_generator.py`)

Bridges the diagnostic report into directly usable developer prompts.

- **Unified Diff Synthesis**: Uses Python `difflib.unified_diff` to produce standard Git `--- a/file` and `+++ b/file` patches.
- **Cursor Composer Formatter**: Outputs Markdown prompts referencing target files (`@FeedScreen.kt`), root causes, and diff code blocks.
- **Antigravity Formatter**: Outputs standard Multi-Agent System (MAS) task specifications (`ROLE: general-dev`, `TASK_ID`, `FILES:`, `DIFF:`, `DONE`).

---

### 2.6 FastAPI Web Backend & Two-Way WebSockets (`apps/server.py`)

- **WebSocket `/ws/stream`**: Bidirectional frame pipeline. Receives frames from Android clients or requests frames via ADB, and broadcasts frames to connected web browsers at up to 15 FPS.
- **WebSocket `/ws/logcat`**: Broadcasts parsed, PII-sanitized logcat lines to browser sessions in real-time.
- **WebSocket `/ws/control`**: Full-duplex command channel supporting actions:
  - `REQUEST_KEYFRAME`: Forces immediate high-res capture.
  - `SET_FPS`: Throttles device capture frame rate.
  - `RESTART_PACKAGE`: Restarts the target Android app via ADB.
  - `CLEAR_LOGCAT`: Clears device and server buffers.
- **REST Endpoints**:
  - `GET /api/health`: Server health and buffer statistics.
  - `GET /api/devices`: List of connected Android devices.
  - `POST /api/diagnose`: Triggers multimodal analysis on current buffer state.
  - `POST /api/patch`: Generates IDE prompt actions for a report.

---

### 2.7 Native Android Streaming App Foundation (`android/`)

The native Android app under `android/` implements a production-grade frame capture pipeline:

- **Target SDK 34 / Android 14 Compliance**: Declares `FOREGROUND_SERVICE_MEDIA_PROJECTION` in `AndroidManifest.xml` and runs `MediaProjectionService` with ongoing system notifications.
- **`MediaProjectionService.kt`**: Obtains `MediaProjection` token from user consent, creates a `VirtualDisplay` mirroring into an `ImageReader` (RGBA_8888), compresses frames to JPEG/WebP, and transmits them over WebSockets.
- **`WebSocketStreamClient.kt`**: Resilient OkHttp client with ping heartbeats, auto-reconnect, and bi-directional command processing (`SET_FPS`, `REQUEST_KEYFRAME`).
- **`MainActivity.kt`**: Jetpack Compose interface allowing the developer to configure the server host/port and toggle live streaming.

---

## 3. Quickstart & Verification Walkthrough

### 3.1 Running the Python Server & Web Dashboard

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Launch FastAPI server
python apps/server.py
```
Open `http://localhost:8000` in any web browser to view the live dashboard.

### 3.2 Running the Offline Test Suite

```bash
python -m pytest tests/test_foundations.py -v
```

All 14 foundation test cases run completely offline without requiring physical hardware.
