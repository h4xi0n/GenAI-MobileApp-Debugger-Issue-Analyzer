"""FastAPI Two-Way Streaming & Debugging Server.

Provides WebSocket channels (/ws/stream, /ws/logcat, /ws/control) and REST APIs
for mobile screen streaming, logcat monitoring, device control, and GenAI diagnostics.

Author: Arun Krishnan
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Set Windows Proactor event loop policy if running on Windows
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.agents.diagnostic_agent import (
    MultimodalDiagnosticAgent,
    create_initial_diagnostic_state,
)
from src.core.adb_manager import ADBManager
from src.core.llm_client import get_multimodal_client
from src.core.logcat_parser import LogcatParser, SlidingLogWindow
from src.core.logger import setup_logger
from src.core.models import (
    DeviceCommand,
    DeviceStatus,
    DiagnosticReport,
    IDEActionPrompt,
    KnowledgeCard,
    LLMUsageMetadata,
    LogEntry,
    ScreenFrame,
    ScreenState,
    WebSocketMessage,
)
from src.core.patch_generator import PatchGenerator
from src.core.screen_inspector import FrameRingBuffer, ScreenInspector
from src.rag.hybrid_retriever import HybridRetriever

logger = setup_logger("server")

# Base directory paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes HybridRetriever and background streaming workers on startup and cleans up on shutdown."""
    global logcat_background_task, screen_background_task, hybrid_retriever
    if hybrid_retriever is None:
        hybrid_retriever = HybridRetriever()
    logger.info("HybridRetriever knowledge base warmed up on startup.")
    logcat_background_task = asyncio.create_task(logcat_worker())
    screen_background_task = asyncio.create_task(screen_stream_worker())
    yield
    if logcat_background_task:
        logcat_background_task.cancel()
    if screen_background_task:
        screen_background_task.cancel()
    adb_manager.stop_streaming()


app = FastAPI(
    title="GenAI Mobile App Debugger & Issue Analyzer",
    description="Two-way mobile screen streaming, real-time logcat triage, and GenAI remediation platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Shared In-Memory State
adb_manager = ADBManager()
screen_inspector = ScreenInspector()
ring_buffer = FrameRingBuffer(capacity=5)
log_window = SlidingLogWindow(max_lines=300)
logcat_parser = LogcatParser()
hybrid_retriever = HybridRetriever()

# Background Task Handles & Streaming Control Flags
logcat_background_task: asyncio.Task | None = None
screen_background_task: asyncio.Task | None = None
is_streaming_active: bool = True

# Active LLM Provider State & Cumulative Telemetry
active_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "mock")
active_llm_model: str | None = None
cumulative_llm_tokens: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}
last_llm_usage: LLMUsageMetadata | None = None


class ConnectionManager:
    """Manages active WebSocket connections across channels."""

    def __init__(self):
        """Initializes empty client sets for stream, logcat, control, and device channels."""
        self.stream_clients: set[WebSocket] = set()
        self.logcat_clients: set[WebSocket] = set()
        self.control_clients: set[WebSocket] = set()
        self.device_clients: set[WebSocket] = set()

    async def connect_stream(self, websocket: WebSocket):
        """Accepts and registers a new WebSocket screen stream client."""
        await websocket.accept()
        self.stream_clients.add(websocket)
        logger.info(f"Stream client connected. Total: {len(self.stream_clients)}")

    def disconnect_stream(self, websocket: WebSocket):
        """Unregisters a disconnected screen stream client."""
        self.stream_clients.discard(websocket)
        logger.info(f"Stream client disconnected. Total: {len(self.stream_clients)}")

    async def broadcast_frame(self, frame: ScreenFrame):
        """Broadcasts a ScreenFrame to all connected UI stream clients."""
        payload = frame.model_dump()
        msg = WebSocketMessage(type="frame", payload=payload).model_dump_json()
        dead_clients = []
        for client in self.stream_clients:
            try:
                await client.send_text(msg)
            except Exception:
                dead_clients.append(client)
        for dead in dead_clients:
            self.stream_clients.discard(dead)

    async def connect_logcat(self, websocket: WebSocket):
        """Accepts and registers a new WebSocket logcat client."""
        await websocket.accept()
        self.logcat_clients.add(websocket)
        logger.info(f"Logcat client connected. Total: {len(self.logcat_clients)}")

    def disconnect_logcat(self, websocket: WebSocket):
        """Unregisters a disconnected logcat client."""
        self.logcat_clients.discard(websocket)
        logger.info(f"Logcat client disconnected. Total: {len(self.logcat_clients)}")

    async def broadcast_log_entry(self, entry: LogEntry):
        """Broadcasts a LogEntry to connected logcat clients."""
        payload = entry.model_dump()
        msg = WebSocketMessage(type="logcat", payload=payload).model_dump_json()
        dead_clients = []
        for client in self.logcat_clients:
            try:
                await client.send_text(msg)
            except Exception:
                dead_clients.append(client)
        for dead in dead_clients:
            self.logcat_clients.discard(dead)

    async def connect_control(self, websocket: WebSocket):
        """Accepts and registers a new WebSocket control client."""
        await websocket.accept()
        self.control_clients.add(websocket)
        logger.info(f"Control client connected. Total: {len(self.control_clients)}")

    def disconnect_control(self, websocket: WebSocket):
        """Unregisters a disconnected control client."""
        self.control_clients.discard(websocket)
        logger.info(f"Control client disconnected. Total: {len(self.control_clients)}")

    async def broadcast_control(self, message: dict[str, Any]):
        """Broadcasts a control message to all control and device clients."""
        msg = json.dumps(message)
        dead = []
        for client in self.control_clients:
            try:
                await client.send_text(msg)
            except Exception:
                dead.append(client)
        for d in dead:
            self.control_clients.discard(d)


manager = ConnectionManager()


async def logcat_worker():
    """Continuously ingests logcat lines from ADBManager and broadcasts to clients.

    Consumes the asynchronous line stream from ADB, parses each entry into a LogEntry,
    stores it in the sliding log window buffer, and broadcasts it across connected
    logcat WebSocket clients. Automatically reconnects with a 2-second backoff
    upon stream failure or disconnection.
    """
    logger.info("Starting logcat ingestion worker...")
    while True:
        try:
            async for line in adb_manager.stream_logcat_lines():
                entry = logcat_parser.parse_line(line)
                if entry:
                    log_window.add(entry)
                    await manager.broadcast_log_entry(entry)
        except asyncio.CancelledError:
            logger.info("Logcat worker task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in logcat worker (reconnecting in 2s): {type(e).__name__}: {e}", exc_info=True)
            try:
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                logger.info("Logcat worker task cancelled during reconnect sleep.")
                break


async def screen_stream_worker():
    """Continuous background worker capturing ADB frames and broadcasting to UI stream clients.

    Runs a frame polling loop throttled to ~2.5 FPS (400ms sleep intervals). Captures
    screenshots from ADB off the main event loop thread, processes raw bytes into
    ScreenFrame models, stores them in the rolling frame ring buffer, and broadcasts
    them to connected /ws/stream clients when streaming is active.
    """
    logger.info("Starting screen stream worker...")
    try:
        while True:
            # Poll only when streaming is active and at least one stream client is connected
            if is_streaming_active and len(manager.stream_clients) > 0:
                try:
                    raw_bytes = await asyncio.to_thread(adb_manager.capture_screenshot)
                    frame = screen_inspector.process_image_bytes(raw_bytes)
                    ring_buffer.append(frame)
                    await manager.broadcast_frame(frame)
                except Exception as e:
                    logger.warning(f"Error capturing or broadcasting frame in stream worker: {e}")
            await asyncio.sleep(0.4)  # ~2.5 FPS throttle interval
    except asyncio.CancelledError:
        logger.info("Screen stream worker task cancelled.")
    except Exception as e:
        logger.error(f"Fatal error in screen stream worker: {e}")


# -----------------------------------------------------------------------------
# REST API Endpoints
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    """Serves the main two-pane debugging dashboard."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/health")
async def get_health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "mock_mode": adb_manager.mock_mode,
        "ring_buffer_frames": ring_buffer.get_count(),
        "buffered_logs": len(log_window.get_entries()),
    }


@app.get("/api/devices", response_model=list[DeviceStatus])
async def list_devices():
    """Returns connected ADB devices or mock devices."""
    return adb_manager.list_devices()


@app.api_route("/api/device/select", methods=["POST", "GET"])
async def select_device(request: Request):
    """Switches the active target ADB device serial.

    Accepts the target serial either from a JSON POST body (`{"serial": "..."}`) or
    URL query parameter (`?serial=...`). Updates `adb_manager.serial` so subsequent
    frame capture, logcat streaming, and device commands target the selected device.

    Args:
        request: FastAPI Request containing JSON body or query parameters.

    Returns:
        Dict indicating status and active device serial.

    Raises:
        HTTPException: 400 Bad Request if serial is not provided.
    """
    serial = None
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                serial = body.get("serial")
            except Exception:
                pass
    if not serial:
        serial = request.query_params.get("serial")
    if not serial:
        raise HTTPException(status_code=400, detail="Device serial is required")

    adb_manager.serial = serial
    logger.info(f"Active device switched to serial: {serial}")
    return {"status": "success", "serial": serial}


@app.get("/api/frames/latest")
async def get_latest_frame():
    """Retrieves the latest captured screen frame."""
    frame = ring_buffer.get_latest()
    if not frame:
        # Fall back to immediate capture
        try:
            raw_bytes = adb_manager.capture_screenshot()
            frame = screen_inspector.process_image_bytes(raw_bytes)
            ring_buffer.append(frame)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to capture frame: {e}")
    return frame


@app.get("/api/llm/status")
async def get_llm_status():
    """Returns current LLM provider, active model, token telemetry, and available providers."""
    providers = [
        {"provider": "mock", "name": "Offline Mock VLM", "available": True, "active": active_llm_provider == "mock"},
        {"provider": "gemini", "name": "Google Gemini (2.0 Flash)", "available": bool(os.getenv("GEMINI_API_KEY")), "active": active_llm_provider == "gemini"},
        {"provider": "claude", "name": "Anthropic Claude (3.5 Sonnet)", "available": bool(os.getenv("ANTHROPIC_API_KEY")), "active": active_llm_provider in ["claude", "anthropic"]},
        {"provider": "openai", "name": "OpenAI (GPT-4o)", "available": bool(os.getenv("OPENAI_API_KEY")), "active": active_llm_provider == "openai"},
    ]
    client = get_multimodal_client(active_llm_provider, active_llm_model)
    return {
        "active_provider": client.provider,
        "active_model": client.model,
        "available_providers": providers,
        "cumulative_tokens": cumulative_llm_tokens,
        "last_usage": last_llm_usage.model_dump() if last_llm_usage else None,
    }


@app.api_route("/api/llm/select", methods=["POST", "GET"])
async def select_llm(request: Request):
    """Switches the active LLM provider and model.

    Accepts parameters from either JSON POST body ({"provider": "...", "model": "..."})
    or URL query parameters (?provider=...&model=...). Updates global provider configuration,
    instantiates the requested client via the factory, and broadcasts an 'llm_status'
    event to all connected control WebSocket clients.

    Args:
        request: FastAPI Request containing JSON body or query parameters.

    Returns:
        Dict indicating status, active provider, and active model.

    Raises:
        HTTPException: 400 Bad Request if provider is not specified.
    """
    global active_llm_provider, active_llm_model
    provider = None
    model = None
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                provider = body.get("provider")
                model = body.get("model")
            except Exception:
                pass
    if not provider:
        provider = request.query_params.get("provider")
        model = request.query_params.get("model") or model
    if not provider:
        raise HTTPException(status_code=400, detail="LLM provider is required")

    active_llm_provider = provider.strip().lower()
    active_llm_model = model
    client = get_multimodal_client(active_llm_provider, active_llm_model)
    logger.info(f"Active LLM switched to provider: {client.provider}, model: {client.model}")

    # Broadcast LLM status update over WebSocket control channel
    await manager.broadcast_control({
        "type": "llm_status",
        "payload": {
            "active_provider": client.provider,
            "active_model": client.model,
            "cumulative_tokens": cumulative_llm_tokens,
        },
    })

    return {
        "status": "success",
        "active_provider": client.provider,
        "active_model": client.model,
    }


@app.post("/api/diagnose", response_model=DiagnosticReport)
async def trigger_diagnose():
    """Synthesizes a multimodal diagnostic report using the Autonomous Agent StateGraph.

    Retrieves current logcat buffer entries from SlidingLogWindow and extracts crash
    signatures. Ingests the latest viewport frame from FrameRingBuffer (or captures an
    immediate on-demand frame). Executes the cyclic ReAct state graph asynchronously,
    broadcasting real-time agent progression step events ('correlator', 'retriever',
    'diagnoser', 'verifier', 'retry') over the WebSocket control channel. Accumulates
    token telemetry, and returns the final verified DiagnosticReport.

    Returns:
        Validated DiagnosticReport including root cause, suggested fix, grounding references, and telemetry.
    """
    global last_llm_usage
    entries = log_window.get_entries()
    crash_sig = logcat_parser.extract_crash_signature(entries)

    latest_frame = ring_buffer.get_latest()
    if not latest_frame:
        raw_bytes = adb_manager.capture_screenshot()
        latest_frame = screen_inspector.process_image_bytes(
            raw_bytes,
            screen_state=ScreenState.CRASH_DIALOG if crash_sig else ScreenState.NORMAL,
        )
        ring_buffer.append(latest_frame)

    pkg_name = crash_sig.package_name if crash_sig else "com.example.mobileapp"

    async def broadcast_agent_step(step_name: str, current_state: dict[str, Any]):
        """Broadcasts real-time agent progression step events over /ws/control."""
        await manager.broadcast_control({
            "type": "agent_step",
            "step": step_name,
            "payload": {
                "node": step_name,
                "retry_count": current_state.get("retry_count", 0),
                "confidence_score": current_state.get("confidence_score", 0.0),
            },
        })

    client = get_multimodal_client(active_llm_provider, active_llm_model)
    agent = MultimodalDiagnosticAgent(
        client=client,
        hybrid_retriever=hybrid_retriever,
        step_callback=broadcast_agent_step,
    )

    initial_state = create_initial_diagnostic_state(
        package_name=pkg_name,
        log_entries=entries,
        screen_frame=latest_frame,
        crash_signature=crash_sig,
        screen_state=latest_frame.screen_state,
        device_meta={"serial": adb_manager.serial or "emulator-5554", "mock_mode": adb_manager.mock_mode},
    )

    try:
        final_state = await agent.ainvoke(initial_state, step_callback=broadcast_agent_step)
        report = final_state.get("report")
    except Exception as e:
        logger.exception(f"Agent state graph execution failed: {e}. Falling back to Mock.")
        fallback_agent = MultimodalDiagnosticAgent(
            client=get_multimodal_client("mock"),
            hybrid_retriever=hybrid_retriever,
            step_callback=broadcast_agent_step,
        )
        final_state = await fallback_agent.ainvoke(initial_state, step_callback=broadcast_agent_step)
        report = final_state.get("report")

    if not report:
        report = DiagnosticReport(
            package_name=pkg_name,
            error_summary="Application Diagnostic Complete",
            root_cause_analysis="No critical anomalies detected during agent state graph evaluation.",
            confidence_score=0.90,
            affected_components=[],
            suggested_fix_summary="Continue monitoring runtime logs and UI states.",
            crash_signature=crash_sig,
        )

    # Attach crash signature if detected by parser but omitted by model
    if crash_sig and not report.crash_signature:
        report.crash_signature = crash_sig

    # Attach retrieved grounding references if omitted by model
    if final_state.get("grounding_cards") and not report.grounding_references:
        report.grounding_references = final_state["grounding_cards"]

    # Update cumulative telemetry
    usage = report.usage
    if usage:
        cumulative_llm_tokens["prompt_tokens"] += usage.prompt_tokens
        cumulative_llm_tokens["completion_tokens"] += usage.completion_tokens
        cumulative_llm_tokens["total_tokens"] += usage.total_tokens
        last_llm_usage = usage

    # Broadcast diagnosis and telemetry over control WebSocket channel
    await manager.broadcast_control({
        "type": "diagnostic_report",
        "payload": report.model_dump(),
        "usage": usage.model_dump() if usage else None,
        "ide_prompts": {
            k: v.model_dump() for k, v in (final_state.get("ide_prompts") or {}).items()
        } if final_state.get("ide_prompts") else None,
    })

    return report


@app.post("/api/patch", response_model=dict[str, IDEActionPrompt])
async def generate_patch(report: DiagnosticReport):
    """Generates both Cursor Composer and Antigravity action prompts for a diagnostic report."""
    orig_code = (
        "package com.example.mobileapp.ui\n\n"
        "@Composable\n"
        "fun FeedScreen(uiState: FeedUiState) {\n"
        "    Column(modifier = Modifier.fillMaxSize()) {\n"
        "        Text(text = uiState.userProfile.name)\n"
        "    }\n"
        "}\n"
    )
    mod_code = (
        "package com.example.mobileapp.ui\n\n"
        "@Composable\n"
        "fun FeedScreen(uiState: FeedUiState) {\n"
        "    Column(modifier = Modifier.fillMaxSize()) {\n"
        "        Text(text = uiState.userProfile?.name ?: \"Guest\")\n"
        "    }\n"
        "}\n"
    )

    cursor_prompt = PatchGenerator.format_cursor_prompt(
        report,
        original_code=orig_code,
        modified_code=mod_code,
    )
    antigravity_prompt = PatchGenerator.format_antigravity_handoff(
        report,
        original_code=orig_code,
        modified_code=mod_code,
    )

    return {
        "cursor": cursor_prompt,
        "antigravity": antigravity_prompt,
    }


@app.api_route("/api/rag/search", methods=["GET", "POST"], response_model=list[KnowledgeCard])
async def rag_search(
    request: Request,
    query: str | None = None,
    top_k: int = 3,
    category: str | None = None,
):
    """Searches the Android SDK Knowledge Base using the HybridRetriever (BM25 + Dense RRF).

    Accepts search parameters via query parameters (?query=...&top_k=3&category=...) or
    JSON POST request body ({"query": "...", "top_k": 3, "category": "..."}).

    Args:
        request: FastAPI Request object.
        query: Search query string or exception snippet.
        top_k: Maximum number of grounded knowledge cards to return.
        category: Optional category filter (e.g. COMPOSE, COROUTINES_ANR, LIFECYCLE).

    Returns:
        Ranked list of KnowledgeCard models with normalized confidence scores.

    Raises:
        HTTPException: 400 Bad Request if query is missing.
    """
    search_query = query
    search_top_k = top_k
    search_category = category

    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    search_query = body.get("query", search_query)
                    search_top_k = body.get("top_k", search_top_k)
                    search_category = body.get("category", search_category)
            except Exception:
                pass

    if not search_query:
        search_query = request.query_params.get("query")

    if not search_query or not search_query.strip():
        raise HTTPException(status_code=400, detail="Search query is required")

    try:
        raw_top_k = request.query_params.get("top_k", search_top_k)
        search_top_k = int(raw_top_k)
    except (TypeError, ValueError):
        search_top_k = 3

    if not search_category:
        search_category = request.query_params.get("category")

    if not hybrid_retriever:
        return []

    return hybrid_retriever.retrieve(
        query=search_query.strip(),
        top_k=search_top_k,
        category=search_category,
    )


@app.post("/api/device/command")
async def execute_command(cmd: DeviceCommand):
    """Executes a device control command."""
    global is_streaming_active
    cmd_type = cmd.type.upper()
    payload = cmd.payload

    if cmd_type == "RESTART_PACKAGE":
        pkg = payload.get("package", "com.example.mobileapp")
        adb_manager.force_stop_package(pkg)
        adb_manager.start_package(pkg)
        return {"status": "success", "message": f"Restarted package {pkg}"}

    elif cmd_type == "CLEAR_PACKAGE_DATA":
        pkg = payload.get("package", "com.example.mobileapp")
        adb_manager.clear_package_data(pkg)
        return {"status": "success", "message": f"Cleared data for {pkg}"}

    elif cmd_type == "REQUEST_KEYFRAME":
        raw_bytes = adb_manager.capture_screenshot()
        frame = screen_inspector.process_image_bytes(raw_bytes)
        ring_buffer.append(frame)
        await manager.broadcast_frame(frame)
        return {"status": "success", "frame_id": frame.frame_id}

    elif cmd_type == "CLEAR_LOGCAT":
        adb_manager.clear_logcat()
        log_window.clear()
        return {"status": "success", "message": "Logcat cleared"}

    elif cmd_type == "TOGGLE_STREAM":
        if "active" in payload:
            is_streaming_active = bool(payload["active"])
        else:
            is_streaming_active = not is_streaming_active
        logger.info(f"TOGGLE_STREAM command executed. is_streaming_active={is_streaming_active}")
        return {"status": "success", "is_streaming_active": is_streaming_active}

    return {"status": "unknown_command", "type": cmd.type}


# -----------------------------------------------------------------------------
# WebSocket Channels
# -----------------------------------------------------------------------------

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket channel for live frame streaming and client stream control.

    Registers connected browser clients, ingests incoming frames from mobile agents,
    serves on-demand frame requests (`request_frame`), and synchronizes streaming state
    (`toggle_stream`). Unregisters clients upon disconnect.
    """
    global is_streaming_active
    await manager.connect_stream(websocket)
    try:
        while True:
            # Handle incoming messages (e.g. from Android client sending frames or UI control)
            message_text = await websocket.receive_text()
            try:
                data = json.loads(message_text)
                msg_type = data.get("type")
                if msg_type == "frame":
                    frame_data = data.get("payload", {})
                    # Ingest frame uploaded from on-device streaming agent
                    frame = ScreenFrame(**frame_data)
                    ring_buffer.append(frame)
                    await manager.broadcast_frame(frame)
                elif msg_type == "request_frame":
                    # Capture and broadcast immediate on-demand screenshot
                    raw_bytes = adb_manager.capture_screenshot()
                    frame = screen_inspector.process_image_bytes(raw_bytes)
                    ring_buffer.append(frame)
                    await manager.broadcast_frame(frame)
                elif msg_type == "toggle_stream":
                    # Toggle or set explicit streaming state
                    if "active" in data.get("payload", {}):
                        is_streaming_active = bool(data["payload"]["active"])
                    else:
                        is_streaming_active = not is_streaming_active
                    logger.info(f"WebSocket toggle_stream: is_streaming_active={is_streaming_active}")
            except Exception as e:
                logger.warning(f"Error handling stream websocket message: {e}")
    except WebSocketDisconnect:
        manager.disconnect_stream(websocket)


@app.websocket("/ws/logcat")
async def websocket_logcat(websocket: WebSocket):
    """WebSocket channel for streaming real-time parsed logcat entries to the UI."""
    await manager.connect_logcat(websocket)
    # Immediately send existing windowed logs so log history appears immediately
    try:
        for entry in log_window.get_entries():
            await websocket.send_text(
                WebSocketMessage(type="logcat", payload=entry.model_dump()).model_dump_json()
            )
        while True:
            # Keep open and handle any incoming filters
            _ = await websocket.receive_text()
            # Client can send filtering configuration if needed
    except WebSocketDisconnect:
        manager.disconnect_logcat(websocket)


@app.websocket("/ws/control")
async def websocket_control(websocket: WebSocket):
    """Bidirectional full-duplex control protocol channel."""
    await manager.connect_control(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                cmd_dict = json.loads(text)
                cmd = DeviceCommand(**cmd_dict)
                # Dispatch command
                await execute_command(cmd)
                # Broadcast command execution confirmation
                await manager.broadcast_control({
                    "type": "command_ack",
                    "command_id": cmd.command_id,
                    "command_type": cmd.type,
                    "status": "executed"
                })
            except Exception as e:
                logger.error(f"Error processing control message: {e}")
    except WebSocketDisconnect:
        manager.disconnect_control(websocket)


if __name__ == "__main__":
    uvicorn.run("apps.server:app", host="0.0.0.0", port=8000, reload=True)
