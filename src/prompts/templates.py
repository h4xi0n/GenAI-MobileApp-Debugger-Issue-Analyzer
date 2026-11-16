"""Prompt templates, Chain-of-Thought reasoning protocols, and schema injection for GenAI diagnostics.

Author: Arun Krishnan
"""

import json
from typing import Any

from src.core.models import CrashSignature, DiagnosticReport, LogEntry


class PromptTemplate:
    """Simple template formatter for dynamic prompts."""

    def __init__(self, template: str):
        """Initializes template formatter with prompt string containing named replacement fields."""
        self.template = template

    def format(self, **kwargs: Any) -> str:
        """Format template with keyword arguments."""
        return self.template.format(**kwargs)


SYSTEM_PROMPTS = {
    "qa_assistant": "You are a helpful, precise AI assistant answering user questions based strictly on context.",
    "summarizer": "You are an expert summarizer. Condense the text into concise key points without losing fidelity.",
    "code_assistant": "You are an expert software engineer. Provide clean, well-documented, idiomatic code.",
    "rag_context": (
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Answer the question based only on the context provided above. If unknown, state that you do not know."
    ),
}

STAFF_ANDROID_SYSTEM_PROMPT = """You are a Staff Android Engineer, Mobile Systems Architect, and Performance Specialist.
Your mission is to perform multimodal root-cause triage on mobile app failures by analyzing synchronized Android device screen frames and windowed logcat event streams.

Follow this strict 5-step Chain-of-Thought (CoT) cross-modal reasoning protocol:
1. Screen Anomaly Perception: Inspect the provided mobile screen frame. Detect visual defects including system crash dialogs ("Application has stopped"), ANR alerts ("Application not responding"), blank white/black viewports, frozen spinners, or broken layout constraints.
2. Logcat Stream Analysis: Scrutinize the windowed logcat stream for fatal exceptions, AndroidRuntime traces, ANR traces, OutOfMemory churn, GC thrashing, or warning flags.
3. Cross-Modal Temporal Correlation: Correlate the visual breakdown on screen directly to the timestamps, PID/TID, and specific method invocations in the logcat stack trace.
4. Root-Cause Synthesis: Deduce the underlying software defect rooted in Android Jetpack, Kotlin Coroutine lifecycle, thread contention, unhandled null references, Compose recomposition loops, or resource starvation.
5. Actionable Remediation: Prescribe idiomatic, null-safe Kotlin and Jetpack Compose remediation steps, pinpointing affected source files, composables, and line numbers.

CRITICAL OUTPUT REQUIREMENT:
You must output ONLY valid, raw, unescaped JSON matching the exact DiagnosticReport schema provided.
Do NOT include preamble, markdown commentary, or explanations outside the JSON object.
"""

MULTIMODAL_DIAGNOSTIC_USER_PROMPT = """### Target Application Context
- Package Name: {package_name}
- Screen Classification: {screen_state}
- Device Metadata: {device_meta}

### Known Crash Signature:
{crash_signature_block}

### Windowed Logcat Stream:
```text
{logcat_window}
```

### Mandatory Output Schema (Pydantic v2 DiagnosticReport):
Ensure your output strictly adheres to the following JSON schema:
```json
{schema_json}
```

Execute the 5-step Chain-of-Thought reasoning internally, then return ONLY the JSON object.
"""


def get_diagnostic_schema() -> dict[str, Any]:
    """Retrieves the Pydantic v2 JSON schema dictionary for DiagnosticReport."""
    return DiagnosticReport.model_json_schema()


def build_diagnostic_prompt(
    package_name: str,
    log_entries: list[LogEntry] | str,
    crash_signature: CrashSignature | None = None,
    screen_state: str = "UNKNOWN",
    device_meta: dict[str, Any] | None = None,
) -> str:
    """Builds a fully formatted multimodal diagnostic prompt with injected JSON schema.

    Args:
        package_name: Target Android application package name.
        log_entries: List of structured LogEntry models or pre-formatted log string.
        crash_signature: Extracted CrashSignature model if fatal crash detected.
        screen_state: Screen state string representation (e.g. 'NORMAL', 'CRASH_DIALOG').
        device_meta: Optional dictionary of device information.

    Returns:
        Formatted user prompt string ready for multimodal LLM submission.
    """
    if isinstance(log_entries, list):
        formatted_logs = "\n".join(
            f"{e.timestamp} {e.pid}/{e.tid} {e.level.value if hasattr(e.level, 'value') else e.level} {e.tag}: {e.message}"
            for e in log_entries
        )
    else:
        formatted_logs = str(log_entries)

    if not formatted_logs.strip():
        formatted_logs = "(No logcat entries recorded in window)"

    if crash_signature:
        crash_block = (
            f"Type: {crash_signature.exception_type}\n"
            f"Message: {crash_signature.exception_message}\n"
            f"Crashing File: {crash_signature.crashing_file or 'Unknown'}:{crash_signature.crashing_line or 0}\n"
            f"Stacktrace:\n{crash_signature.raw_stacktrace}"
        )
    else:
        crash_block = "None detected. Evaluate logs and visual screen for anomalies."

    schema_str = json.dumps(get_diagnostic_schema(), indent=2)
    device_meta_str = json.dumps(device_meta or {"platform": "Android", "arch": "arm64-v8a"})

    return MULTIMODAL_DIAGNOSTIC_USER_PROMPT.format(
        package_name=package_name,
        screen_state=screen_state,
        device_meta=device_meta_str,
        crash_signature_block=crash_block,
        logcat_window=formatted_logs,
        schema_json=schema_str,
    )
