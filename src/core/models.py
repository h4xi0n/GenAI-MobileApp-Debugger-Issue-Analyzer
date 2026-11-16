"""Data contracts and Pydantic v2 schemas for the GenAI Mobile App Debugger.

Author: Arun Krishnan
"""

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LogLevel(str, Enum):
    """Standard Android logcat severity levels."""
    VERBOSE = "V"
    DEBUG = "D"
    INFO = "I"
    WARN = "W"
    ERROR = "E"
    FATAL = "F"


class LogEntry(BaseModel):
    """Structured representation of a single Android logcat entry."""
    timestamp: str = Field(description="Normalized timestamp string (e.g. '11-15 12:00:01.123')")
    pid: int = Field(description="Process ID", ge=0)
    tid: int = Field(description="Thread ID", ge=0)
    level: LogLevel = Field(description="Log severity level")
    tag: str = Field(description="Logcat logging tag", max_length=128)
    message: str = Field(description="Sanitized log message payload")
    is_stacktrace: bool = Field(default=False, description="True if line belongs to an exception stack trace")

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, v: str) -> str:
        """Strips surrounding whitespace from logcat tag string."""
        return v.strip()


class ScreenState(str, Enum):
    """Visual anomaly and UI state classifications."""
    NORMAL = "NORMAL"
    CRASH_DIALOG = "CRASH_DIALOG"
    ANR_DIALOG = "ANR_DIALOG"
    BLANK_SCREEN = "BLANK_SCREEN"
    FROZEN_UI = "FROZEN_UI"
    LAYOUT_GLITCH = "LAYOUT_GLITCH"


class ScreenFrame(BaseModel):
    """Captured device screen keyframe with compression and visual state annotation."""
    frame_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique frame identifier")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 or logcat-aligned timestamp"
    )
    image_bytes_base64: str = Field(description="Base64-encoded image payload (JPEG/WebP)")
    mime_type: Literal["image/jpeg", "image/webp", "image/png"] = Field(default="image/webp")
    width: int = Field(description="Image pixel width", gt=0)
    height: int = Field(description="Image pixel height", gt=0)
    screen_state: ScreenState = Field(default=ScreenState.NORMAL, description="Visual anomaly classification")


class CrashSignature(BaseModel):
    """Extracted exception and failure signature from logcat analysis."""
    exception_type: str = Field(description="Fully qualified exception name (e.g. 'java.lang.NullPointerException')")
    exception_message: str = Field(description="Primary exception message text")
    package_name: str = Field(description="Offending application package name")
    crashing_file: str | None = Field(default=None, description="Source file pinpointed in stack trace")
    crashing_line: int | None = Field(default=None, description="Line number pinpointed in stack trace", ge=1)
    raw_stacktrace: str = Field(description="Deduplicated, complete stack trace block")


class LLMUsageMetadata(BaseModel):
    """Token usage and latency telemetry for multimodal LLM inferences."""
    provider: str = Field(description="LLM provider name (e.g. gemini, claude, openai, mock)")
    model_name: str = Field(description="Model identifier (e.g. gemini-2.0-flash, mock-vlm-v1)")
    prompt_tokens: int = Field(default=0, ge=0, description="Input prompt tokens consumed")
    completion_tokens: int = Field(default=0, ge=0, description="Output completion tokens generated")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens consumed in inference call")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Inference latency in milliseconds")


class DiagnosticReport(BaseModel):
    """Validated output of the Multimodal Diagnostic Engine."""
    report_id: str = Field(default_factory=lambda: f"RPT-{uuid.uuid4().hex[:8].upper()}", description="Unique diagnostic report ID")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="Timestamp of diagnosis"
    )
    package_name: str = Field(description="Target application package name")
    error_summary: str = Field(description="Executive summary of the detected fault", max_length=256)
    root_cause_analysis: str = Field(description="In-depth technical root cause linking UI symptoms to logcat events")
    confidence_score: float = Field(description="Confidence rating between 0.0 and 1.0", ge=0.0, le=1.0)
    affected_components: list[str] = Field(default_factory=list, description="List of source files, Composable names, or activities")
    suggested_fix_summary: str = Field(description="Concise description of the suggested remediation strategy")
    crash_signature: CrashSignature | None = Field(default=None, description="Structured crash details if fatal crash")
    usage: LLMUsageMetadata | None = Field(default=None, description="LLM inference usage telemetry")


class IDEActionPrompt(BaseModel):
    """Structured remediation instructions tailored for IDE AI agents."""
    target_ide: Literal["cursor", "antigravity"] = Field(description="Target IDE platform")
    file_targets: list[str] = Field(description="List of target source files to modify")
    instructions: str = Field(description="Step-by-step developer instructions for remediation")
    proposed_diff: str | None = Field(default=None, description="Syntactically valid unified diff proposal")
    formatted_prompt: str = Field(description="Ready-to-paste markdown prompt for the IDE agent")


class DeviceCommand(BaseModel):
    """Control protocol message for two-way device communication."""
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Command unique ID")
    type: str = Field(description="Command action type, e.g. REQUEST_KEYFRAME, SET_FPS, RESTART_PACKAGE")
    payload: dict[str, Any] = Field(default_factory=dict, description="Command parameters or data payload")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="Timestamp of command dispatch"
    )


class DeviceStatus(BaseModel):
    """Connected device status model."""
    serial: str = Field(description="ADB device serial number or identifier")
    model: str = Field(default="Unknown", description="Device model name")
    state: str = Field(default="device", description="ADB state: device, offline, unauthorized")
    battery_level: int | None = Field(default=None, ge=0, le=100, description="Battery level percentage")
    is_streaming: bool = Field(default=False, description="Whether screen stream is currently active")
    active_package: str | None = Field(default=None, description="Currently foregrounded package name")


class WebSocketMessage(BaseModel):
    """Generic envelope for bidirectional WebSocket messaging."""
    type: str = Field(description="Message type header, e.g. frame, logcat, control, status, error")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 timestamp"
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="Arbitrary typed payload dictionary")
