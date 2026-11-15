"""Logcat Stream Parser, Exception Isolator, and PII Sanitizer.

Provides high-throughput log ingestion, threadtime parsing, fatal crash isolation,
regex PII redaction, and token-bounded sliding window buffering.

Author: Arun Krishnan
"""

import asyncio
import re
from collections import deque
from collections.abc import AsyncGenerator
from typing import ClassVar, Pattern

from .logger import setup_logger
from .models import CrashSignature, LogEntry, LogLevel

logger = setup_logger("logcat_parser")


class PIISanitizer:
    """Regex-based PII redaction engine scrubbing secrets, tokens, and personal data."""

    PATTERNS: ClassVar[list[tuple[Pattern[str], str]]] = [
        # Bearer / Auth tokens
        (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]+"), "Bearer [REDACTED_TOKEN]"),
        # JWT tokens (three base64url segments separated by dots)
        (re.compile(r"eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+"), "[REDACTED_JWT]"),
        # Email addresses
        (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"), "[REDACTED_EMAIL]"),
        # UUIDs
        (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "[REDACTED_UUID]"),
        # IPv4 addresses (excluding 127.0.0.1 and 0.0.0.0 if desired, or all external)
        (re.compile(r"\b(?!127\.0\.0\.1)(?!0\.0\.0\.0)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"), "[REDACTED_IP]"),
    ]

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Applies all redaction rules to input text."""
        sanitized = text
        for pattern, replacement in cls.PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized


class LogcatParser:
    """Parses raw Android threadtime log lines into structured LogEntry records."""

    THREADTIME_REGEX = re.compile(
        r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+([^:]+):\s*(.*)$"
    )

    STACKTRACE_INDICATORS = (
        "at ",
        "\tat ",
        "Caused by:",
        "FATAL EXCEPTION",
        "AndroidRuntime",
        "... ",
        "Process:",
    )

    FILE_LINE_REGEX = re.compile(r"\(([\w\.\-]+):(\d+)\)")
    PROCESS_PKG_REGEX = re.compile(r"Process:\s+([a-zA-Z0-9_\.]+)")

    def parse_line(self, line: str, default_timestamp: str = "01-01 00:00:00.000") -> LogEntry | None:
        """Parses a single log line into LogEntry.

        Handles both standard threadtime lines and continuation stack trace lines.
        """
        line_clean = line.strip("\r\n")
        if not line_clean:
            return None

        match = self.THREADTIME_REGEX.match(line_clean)
        if match:
            timestamp, pid_s, tid_s, level_s, tag, message = match.groups()
            is_stack = any(ind in message for ind in self.STACKTRACE_INDICATORS)
            sanitized_msg = PIISanitizer.sanitize(message)
            return LogEntry(
                timestamp=timestamp,
                pid=int(pid_s),
                tid=int(tid_s),
                level=LogLevel(level_s),
                tag=tag.strip(),
                message=sanitized_msg,
                is_stacktrace=is_stack,
            )

        # Handle stack trace or continuation lines without threadtime header
        is_stack = any(ind in line_clean for ind in self.STACKTRACE_INDICATORS) or line_clean.startswith("\tat ")
        sanitized_msg = PIISanitizer.sanitize(line_clean)
        return LogEntry(
            timestamp=default_timestamp,
            pid=0,
            tid=0,
            level=LogLevel.ERROR if is_stack else LogLevel.INFO,
            tag="AndroidRuntime" if is_stack else "System",
            message=sanitized_msg,
            is_stacktrace=is_stack,
        )

    def extract_crash_signature(self, entries: list[LogEntry], fallback_package: str = "com.example.app") -> CrashSignature | None:
        """Extracts structured crash information from a list of LogEntry records."""
        is_in_crash = False
        crash_lines: list[str] = []
        exception_type = "java.lang.Exception"
        exception_message = "Unknown fatal exception"
        package_name = fallback_package
        crashing_file: str | None = None
        crashing_line: int | None = None

        for entry in entries:
            msg = entry.message
            if "FATAL EXCEPTION" in msg or "AndroidRuntime" in entry.tag:
                is_in_crash = True

            if is_in_crash:
                crash_lines.append(f"{entry.tag}: {msg}")
                # Check for process package
                pkg_match = self.PROCESS_PKG_REGEX.search(msg)
                if pkg_match:
                    package_name = pkg_match.group(1)

                # Check for Exception class line (e.g. java.lang.NullPointerException: ...)
                if "Exception:" in msg or "Error:" in msg or "RuntimeException" in msg:
                    parts = msg.split(":", 1)
                    if len(parts) >= 2:
                        possible_type = parts[0].strip().split()[-1]
                        if "." in possible_type or "Exception" in possible_type or "Error" in possible_type:
                            exception_type = possible_type
                            exception_message = parts[1].strip()

                # Extract file and line from first stack trace line matching (File.kt:123)
                if not crashing_file and ("\tat " in msg or entry.is_stacktrace):
                    file_match = self.FILE_LINE_REGEX.search(msg)
                    if file_match:
                        crashing_file = file_match.group(1)
                        crashing_line = int(file_match.group(2))

        if not crash_lines:
            return None

        return CrashSignature(
            exception_type=exception_type,
            exception_message=exception_message,
            package_name=package_name,
            crashing_file=crashing_file,
            crashing_line=crashing_line,
            raw_stacktrace="\n".join(crash_lines),
        )


class SlidingLogWindow:
    """Maintains a bounded FIFO buffer of log entries with token budgeting."""

    def __init__(self, max_lines: int = 300, max_tokens: int = 4000):
        self.max_lines = max_lines
        self.max_tokens = max_tokens
        self._buffer: deque[LogEntry] = deque(maxlen=max_lines)

    def add(self, entry: LogEntry):
        """Appends a new log entry to the buffer."""
        self._buffer.append(entry)

    def clear(self):
        """Clears all buffered entries."""
        self._buffer.clear()

    def get_entries(self) -> list[LogEntry]:
        """Returns all currently buffered entries."""
        return list(self._buffer)

    def get_window_text(self, priority_levels: list[LogLevel] | None = None) -> str:
        """Returns formatted string of buffered lines within token constraints.

        Approximates tokens as ~4 characters per token.
        """
        lines = []
        for entry in self._buffer:
            if priority_levels and entry.level not in priority_levels:
                continue
            lines.append(f"{entry.timestamp} {entry.pid} {entry.tid} {entry.level.value} {entry.tag}: {entry.message}")

        # Enforce approximate token limit from bottom up
        total_chars = 0
        max_chars = self.max_tokens * 4
        selected_lines = []
        for line in reversed(lines):
            line_len = len(line) + 1
            if total_chars + line_len > max_chars and selected_lines:
                break
            selected_lines.append(line)
            total_chars += line_len

        return "\n".join(reversed(selected_lines))


class LogcatStreamer:
    """Streams logcat lines from ADBManager or a file fixture."""

    def __init__(self, parser: LogcatParser | None = None):
        self.parser = parser or LogcatParser()

    async def stream_from_generator(self, line_generator: AsyncGenerator[str, None]) -> AsyncGenerator[LogEntry, None]:
        """Consumes lines from an async generator, parsing and yielding LogEntry objects."""
        async for line in line_generator:
            parsed = self.parser.parse_line(line)
            if parsed:
                yield parsed

    async def stream_from_fixture(self, fixture_path: str, delay_seconds: float = 0.0) -> AsyncGenerator[LogEntry, None]:
        """Replays log lines from a file fixture line by line."""
        with open(fixture_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                parsed = self.parser.parse_line(line)
                if parsed:
                    yield parsed
