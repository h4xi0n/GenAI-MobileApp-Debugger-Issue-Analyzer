"""Core utilities, clients, models, and ingestion modules."""

from .adb_manager import ADBManager
from .llm_client import (
    BaseMultimodalLLMClient,
    ClaudeMultimodalClient,
    GeminiMultimodalClient,
    HuggingFaceLocalLLM,
    MockMultimodalClient,
    OpenAIMultimodalClient,
    get_multimodal_client,
)
from .logcat_parser import LogcatParser, LogcatStreamer, PIISanitizer, SlidingLogWindow
from .logger import setup_logger
from .models import (
    CrashSignature,
    DeviceCommand,
    DeviceStatus,
    DiagnosticReport,
    IDEActionPrompt,
    LLMUsageMetadata,
    LogEntry,
    LogLevel,
    ScreenFrame,
    ScreenState,
    WebSocketMessage,
)
from .patch_generator import PatchGenerator
from .screen_inspector import FrameRingBuffer, ScreenInspector

__all__ = [
    "ADBManager",
    "BaseMultimodalLLMClient",
    "ClaudeMultimodalClient",
    "CrashSignature",
    "DeviceCommand",
    "DeviceStatus",
    "DiagnosticReport",
    "FrameRingBuffer",
    "GeminiMultimodalClient",
    "HuggingFaceLocalLLM",
    "IDEActionPrompt",
    "LLMUsageMetadata",
    "LogEntry",
    "LogLevel",
    "LogcatParser",
    "LogcatStreamer",
    "MockMultimodalClient",
    "OpenAIMultimodalClient",
    "PIISanitizer",
    "PatchGenerator",
    "ScreenFrame",
    "ScreenInspector",
    "ScreenState",
    "SlidingLogWindow",
    "WebSocketMessage",
    "get_multimodal_client",
    "setup_logger",
]
