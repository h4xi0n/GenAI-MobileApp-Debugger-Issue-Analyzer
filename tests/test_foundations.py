"""Foundations Verification Test Suite.

Validates core data models, ADB manager, logcat parsing, PII sanitization,
screen inspection, rolling ring buffer, patch synthesis, and server APIs offline.

Author: Arun Krishnan
"""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from apps.server import app
from src.core.adb_manager import ADBManager
from src.core.logcat_parser import LogcatParser, PIISanitizer, SlidingLogWindow
from src.core.models import (
    DiagnosticReport,
    IDEActionPrompt,
    LogEntry,
    LogLevel,
    ScreenFrame,
    ScreenState,
)
from src.core.patch_generator import PatchGenerator
from src.core.screen_inspector import FrameRingBuffer, ScreenInspector

# -----------------------------------------------------------------------------
# 1. Models & Data Contracts Tests
# -----------------------------------------------------------------------------

def test_models_log_entry_valid():
    entry = LogEntry(
        timestamp="11-15 12:00:00.123",
        pid=1234,
        tid=5678,
        level=LogLevel.ERROR,
        tag=" AndroidRuntime ",
        message="FATAL EXCEPTION: main",
        is_stacktrace=True,
    )
    assert entry.level == LogLevel.ERROR
    assert entry.tag == "AndroidRuntime"
    assert entry.is_stacktrace is True
    json_data = entry.model_dump_json()
    assert "FATAL EXCEPTION" in json_data


def test_models_screen_frame_validation():
    img = Image.new("RGB", (100, 200), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b64_str = base64.b64encode(buf.getvalue()).decode("ascii")

    frame = ScreenFrame(
        image_bytes_base64=b64_str,
        mime_type="image/jpeg",
        width=100,
        height=200,
        screen_state=ScreenState.NORMAL,
    )
    assert frame.width == 100
    assert frame.height == 200
    assert frame.screen_state == ScreenState.NORMAL


def test_models_diagnostic_report_and_patch():
    report = DiagnosticReport(
        package_name="com.example.mobileapp",
        error_summary="NullPointerException in FeedScreen",
        root_cause_analysis="uiState.userProfile is null",
        confidence_score=0.95,
        affected_components=["FeedScreen.kt"],
        suggested_fix_summary="Add safe null navigation (?.)",
    )
    assert report.confidence_score == 0.95
    assert report.report_id.startswith("RPT-")

    prompt = IDEActionPrompt(
        target_ide="cursor",
        file_targets=["FeedScreen.kt"],
        instructions="Apply null safety fix",
        formatted_prompt="### Cursor Prompt",
    )
    assert prompt.target_ide == "cursor"


# -----------------------------------------------------------------------------
# 2. ADB Manager Tests
# -----------------------------------------------------------------------------

def test_adb_manager_mock_mode():
    mgr = ADBManager(mock_mode=True)
    assert mgr.check_connection() is True
    devices = mgr.list_devices()
    assert len(devices) > 0
    assert devices[0].state == "device"

    # Capture mock screenshot
    png_bytes = mgr.capture_screenshot()
    assert len(png_bytes) > 0
    img = Image.open(io.BytesIO(png_bytes))
    assert img.size == (720, 1280)

    # Lifecycle methods
    assert mgr.clear_logcat() is True
    assert mgr.start_package("com.example.mobileapp") is True
    assert mgr.force_stop_package("com.example.mobileapp") is True
    assert mgr.clear_package_data("com.example.mobileapp") is True
    assert mgr.reverse_port(8000, 8000) is True


@pytest.mark.asyncio
async def test_adb_manager_mock_logcat_streaming():
    mgr = ADBManager(mock_mode=True)
    lines = []
    async for line in mgr.stream_logcat_lines():
        lines.append(line)
        if len(lines) >= 5:
            break
    assert len(lines) == 5
    assert "ActivityManager" in lines[0]


# -----------------------------------------------------------------------------
# 3. Logcat Parser & PII Sanitizer Tests
# -----------------------------------------------------------------------------

def test_pii_sanitizer():
    raw_text = (
        "User session token=Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c "
        "and email user@example.com connected from 192.168.1.100 with device 12345678-1234-5678-1234-567812345678"
    )
    sanitized = PIISanitizer.sanitize(raw_text)
    assert "Bearer [REDACTED_TOKEN]" in sanitized
    assert "[REDACTED_EMAIL]" in sanitized
    assert "[REDACTED_UUID]" in sanitized
    assert "192.168.1.100" not in sanitized
    assert "user@example.com" not in sanitized


def test_logcat_parser_threadtime_and_crash():
    parser = LogcatParser()
    sample_lines = [
        "11-15 12:00:01.100  1000  1050 I ActivityManager: Start proc 2345:com.example.mobileapp/u0a120",
        "11-15 12:00:02.000  2345  2345 E AndroidRuntime: FATAL EXCEPTION: main",
        "11-15 12:00:02.001  2345  2345 E AndroidRuntime: Process: com.example.mobileapp, PID: 2345",
        "11-15 12:00:02.002  2345  2345 E AndroidRuntime: java.lang.NullPointerException: Attempt to invoke virtual method on null reference",
        "11-15 12:00:02.003  2345  2345 E AndroidRuntime: \tat com.example.mobileapp.ui.FeedScreenKt.FeedScreen(FeedScreen.kt:42)",
    ]

    entries = [parser.parse_line(l) for l in sample_lines if parser.parse_line(l) is not None]
    assert len(entries) == 5
    assert entries[0].level == LogLevel.INFO
    assert entries[1].level == LogLevel.ERROR
    assert entries[4].is_stacktrace is True

    sig = parser.extract_crash_signature(entries)
    assert sig is not None
    assert sig.exception_type == "java.lang.NullPointerException"
    assert sig.package_name == "com.example.mobileapp"
    assert sig.crashing_file == "FeedScreen.kt"
    assert sig.crashing_line == 42


def test_sliding_log_window():
    window = SlidingLogWindow(max_lines=5, max_tokens=200)
    for i in range(10):
        window.add(LogEntry(
            timestamp="11-15 12:00:00.000",
            pid=100,
            tid=200,
            level=LogLevel.INFO if i % 2 == 0 else LogLevel.ERROR,
            tag="Test",
            message=f"Log message {i}",
        ))
    entries = window.get_entries()
    assert len(entries) == 5
    assert entries[-1].message == "Log message 9"

    filtered_text = window.get_window_text(priority_levels=[LogLevel.ERROR])
    assert "Log message 9" in filtered_text
    assert "Log message 8" not in filtered_text


# -----------------------------------------------------------------------------
# 4. Screen Inspector & Ring Buffer Tests
# -----------------------------------------------------------------------------

def test_screen_inspector_downscaling():
    inspector = ScreenInspector(max_dimension=1024, default_format="image/webp", quality=80)
    # Create large image 1440x2560
    large_img = Image.new("RGB", (1440, 2560), color=(50, 100, 150))
    buf = io.BytesIO()
    large_img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    frame = inspector.process_image_bytes(raw_bytes)
    assert frame.height == 1024
    assert frame.width == int((1440 * 1024) / 2560)
    assert frame.mime_type == "image/webp"
    assert len(frame.image_bytes_base64) > 0


def test_frame_ring_buffer_rotation():
    ring = FrameRingBuffer(capacity=3)
    inspector = ScreenInspector()
    img = Image.new("RGB", (100, 100), color=(1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    raw_bytes = buf.getvalue()

    for i in range(5):
        frame = inspector.process_image_bytes(raw_bytes, frame_id=f"frame-{i}")
        ring.append(frame)

    assert ring.get_count() == 3
    assert ring.get_latest().frame_id == "frame-4"
    all_frames = ring.get_all()
    assert [f.frame_id for f in all_frames] == ["frame-2", "frame-3", "frame-4"]


# -----------------------------------------------------------------------------
# 5. Patch Generator Tests
# -----------------------------------------------------------------------------

def test_patch_generator_unified_diff():
    orig = "val x = 1\nval y = 2\n"
    mod = "val x = 1\nval y = 3\n"
    diff = PatchGenerator.generate_unified_diff(orig, mod, fromfile="a/code.kt", tofile="b/code.kt")
    assert "--- a/code.kt" in diff
    assert "+++ b/code.kt" in diff
    assert "-val y = 2" in diff
    assert "+val y = 3" in diff


def test_patch_generator_ide_formats():
    report = DiagnosticReport(
        report_id="RPT-TEST1",
        package_name="com.example.mobileapp",
        error_summary="NullPointerException in FeedScreen.kt:42",
        root_cause_analysis="Null dereference on uiState.userProfile",
        confidence_score=0.96,
        affected_components=["app/src/main/java/com/example/ui/FeedScreen.kt"],
        suggested_fix_summary="Add safe-call operator (?.)",
    )

    orig = "Text(text = uiState.userProfile.name)\n"
    mod = "Text(text = uiState.userProfile?.name ?: \"Guest\")\n"

    cursor_prompt = PatchGenerator.format_cursor_prompt(
        report,
        target_file="app/src/main/java/com/example/ui/FeedScreen.kt",
        original_code=orig,
        modified_code=mod,
    )
    assert cursor_prompt.target_ide == "cursor"
    assert "@app/src/main/java/com/example/ui/FeedScreen.kt" in cursor_prompt.formatted_prompt
    assert "```diff" in cursor_prompt.formatted_prompt

    antigravity_handoff = PatchGenerator.format_antigravity_handoff(
        report,
        target_file="app/src/main/java/com/example/ui/FeedScreen.kt",
        original_code=orig,
        modified_code=mod,
        task_id="FIX-TEST1",
    )
    assert antigravity_handoff.target_ide == "antigravity"
    assert "ROLE: general-dev" in antigravity_handoff.formatted_prompt
    assert "TASK_ID: FIX-TEST1" in antigravity_handoff.formatted_prompt
    assert "DONE" in antigravity_handoff.formatted_prompt


# -----------------------------------------------------------------------------
# 6. Server REST API Tests
# -----------------------------------------------------------------------------

def test_server_rest_endpoints():
    client = TestClient(app)

    # Health
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "healthy"

    # Devices
    r_devices = client.get("/api/devices")
    assert r_devices.status_code == 200
    assert isinstance(r_devices.json(), list)

    # Diagnose
    r_diag = client.post("/api/diagnose")
    assert r_diag.status_code == 200
    diag_data = r_diag.json()
    assert "report_id" in diag_data
    assert "confidence_score" in diag_data

    # Patch
    r_patch = client.post("/api/patch", json=diag_data)
    assert r_patch.status_code == 200
    patches = r_patch.json()
    assert "cursor" in patches
    assert "antigravity" in patches

    # Device command
    cmd = {"type": "RESTART_PACKAGE", "payload": {"package": "com.example.mobileapp"}}
    r_cmd = client.post("/api/device/command", json=cmd)
    assert r_cmd.status_code == 200
    assert r_cmd.json()["status"] == "success"


# -----------------------------------------------------------------------------
# 7. Privacy Compliance Gate Test
# -----------------------------------------------------------------------------

def test_privacy_compliance_check():
    """Verifies that no personal background details exist in src or apps."""
    import os
    import re

    forbidden_patterns = [
        re.compile(r"\bcisco\b", re.IGNORECASE),
        re.compile(r"\bintel\b", re.IGNORECASE),
        re.compile(r"\binfosys\b", re.IGNORECASE),
        re.compile(r"\btcs\b", re.IGNORECASE),
        re.compile(r"\bwipro\b", re.IGNORECASE),
        re.compile(r"\bbits\s+pilani\b", re.IGNORECASE),
        re.compile(r"\bstanford\b", re.IGNORECASE),
        re.compile(r"\biit\b", re.IGNORECASE),
        re.compile(r"\bnit\b", re.IGNORECASE),
        re.compile(r"\bbangalore\b", re.IGNORECASE),
        re.compile(r"\bbengaluru\b", re.IGNORECASE),
        re.compile(r"\bcalifornia\b", re.IGNORECASE),
        re.compile(r"\bsan\s+jose\b", re.IGNORECASE),
        re.compile(r"\brtx\s*4090\b", re.IGNORECASE),
        re.compile(r"\balienware\b", re.IGNORECASE),
        re.compile(r"\bmacbook\s+m3\s+max\b", re.IGNORECASE),
        re.compile(r"\bplaystation\b", re.IGNORECASE),
    ]

    files_to_scan = []
    for root, _, files in os.walk("src"):
        for f in files:
            if f.endswith(".py"):
                files_to_scan.append(os.path.join(root, f))
    for root, _, files in os.walk("apps"):
        for f in files:
            if f.endswith((".py", ".js", ".html", ".css")):
                files_to_scan.append(os.path.join(root, f))

    for filepath in files_to_scan:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            for pattern in forbidden_patterns:
                match = pattern.search(content)
                assert match is None, f"Privacy violation: '{match.group(0)}' found in {filepath}"
