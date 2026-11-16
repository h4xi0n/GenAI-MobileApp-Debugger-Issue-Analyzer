"""Unit and integration test suite for the Multimodal Diagnostic Engine.

Validates multi-provider LLM gateway, offline mock synthesis, prompt engineering,
Pydantic v2 schema extraction, server endpoints, and privacy compliance.

Author: Arun Krishnan
"""

import pytest
from fastapi.testclient import TestClient

from apps.server import app
from src.core.llm_client import (
    BaseMultimodalLLMClient,
    ClaudeMultimodalClient,
    GeminiMultimodalClient,
    MockMultimodalClient,
    OpenAIMultimodalClient,
    get_multimodal_client,
)
from src.core.models import (
    CrashSignature,
    DiagnosticReport,
    LogEntry,
    LogLevel,
)
from src.prompts.templates import (
    STAFF_ANDROID_SYSTEM_PROMPT,
    build_diagnostic_prompt,
    get_diagnostic_schema,
)

client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. MockMultimodalClient Deterministic Synthesis Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_multimodal_client_crash_synthesis():
    """Validates MockMultimodalClient synthesizes schema-valid reports for fatal crash prompts."""
    mock_client = MockMultimodalClient(model="mock-vlm-v1")
    prompt = (
        "Package Name: com.example.mobileapp\n"
        "Screen Classification: CRASH_DIALOG\n"
        "FATAL EXCEPTION: java.lang.NullPointerException in FeedScreen.kt:42\n"
        "Attempt to invoke virtual method on a null object reference\n"
    )
    fake_img_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    report, usage = await mock_client.diagnose(
        prompt=prompt,
        image_bytes_base64=fake_img_b64,
        mime_type="image/png",
    )

    assert isinstance(report, DiagnosticReport)
    assert report.report_id.startswith("RPT-")
    assert report.package_name == "com.example.mobileapp"
    assert "NullPointerException" in report.error_summary
    assert report.confidence_score >= 0.90
    assert len(report.affected_components) > 0
    assert "FeedScreen.kt" in report.affected_components[0]
    assert report.crash_signature is not None
    assert report.crash_signature.crashing_file == "FeedScreen.kt"
    assert report.crash_signature.crashing_line == 42
    assert report.usage is not None
    assert usage.provider == "mock"
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
    assert usage.latency_ms > 0


@pytest.mark.asyncio
async def test_mock_multimodal_client_normal_synthesis():
    """Validates MockMultimodalClient synthesizes clean observation reports for normal UI state."""
    mock_client = MockMultimodalClient(model="mock-vlm-v1")
    prompt = (
        "Package Name: com.example.mobileapp\n"
        "Screen Classification: NORMAL\n"
        "Windowed Logcat: 11-15 12:00:00 I ActivityManager: Displayed com.example.mobileapp/.MainActivity: +450ms\n"
    )
    fake_img_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    report, usage = await mock_client.diagnose(
        prompt=prompt,
        image_bytes_base64=fake_img_b64,
    )

    assert isinstance(report, DiagnosticReport)
    assert "No Fatal Crash" in report.error_summary
    assert report.confidence_score >= 0.90
    assert len(report.affected_components) == 0
    assert report.crash_signature is None
    assert usage.prompt_tokens > 0


@pytest.mark.asyncio
async def test_mock_multimodal_client_anr_synthesis():
    """Validates MockMultimodalClient synthesizes ANR diagnosis when ANR pattern is detected."""
    mock_client = MockMultimodalClient(model="mock-vlm-v1")
    prompt = (
        "Package Name: com.example.mobileapp\n"
        "Screen Classification: ANR_DIALOG\n"
        "ANR in com.example.mobileapp (com.example.mobileapp/.MainActivity)\n"
        "Reason: Input dispatching timed out (Waiting to send key event)\n"
    )
    fake_img_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    report, usage = await mock_client.diagnose(
        prompt=prompt,
        image_bytes_base64=fake_img_b64,
    )

    assert "ANR" in report.error_summary or "ANR" in report.root_cause_analysis
    assert report.confidence_score >= 0.90
    assert report.crash_signature is not None
    assert "ANRError" in report.crash_signature.exception_type
    assert usage.prompt_tokens > 0
    assert usage.latency_ms > 0


# -----------------------------------------------------------------------------
# 2. Prompt Engineering & Schema Injection Tests
# -----------------------------------------------------------------------------

def test_prompt_template_schema_injection():
    """Asserts build_diagnostic_prompt formats 5-step CoT and injects valid Pydantic JSON schema."""
    entries = [
        LogEntry(
            timestamp="11-15 12:00:01.000",
            pid=2000,
            tid=2000,
            level=LogLevel.ERROR,
            tag="AndroidRuntime",
            message="FATAL EXCEPTION: main",
            is_stacktrace=True,
        ),
    ]
    crash_sig = CrashSignature(
        exception_type="java.lang.NullPointerException",
        exception_message="Null state",
        package_name="com.example.mobileapp",
        crashing_file="ProfileScreen.kt",
        crashing_line=15,
        raw_stacktrace="at ProfileScreen.kt:15",
    )

    user_prompt = build_diagnostic_prompt(
        package_name="com.example.mobileapp",
        log_entries=entries,
        crash_signature=crash_sig,
        screen_state="CRASH_DIALOG",
    )

    assert "com.example.mobileapp" in user_prompt
    assert "ProfileScreen.kt:15" in user_prompt
    assert "FATAL EXCEPTION" in user_prompt
    assert "properties" in user_prompt
    assert "DiagnosticReport" in user_prompt or "report_id" in user_prompt

    # Verify injected schema is valid JSON
    schema_dict = get_diagnostic_schema()
    assert "properties" in schema_dict
    assert "report_id" in schema_dict["properties"]
    assert "root_cause_analysis" in schema_dict["properties"]

    # Verify CoT protocol in system prompt
    assert "1. Screen Anomaly Perception" in STAFF_ANDROID_SYSTEM_PROMPT
    assert "2. Logcat Stream Analysis" in STAFF_ANDROID_SYSTEM_PROMPT
    assert "3. Cross-Modal Temporal Correlation" in STAFF_ANDROID_SYSTEM_PROMPT
    assert "4. Root-Cause Synthesis" in STAFF_ANDROID_SYSTEM_PROMPT
    assert "5. Actionable Remediation" in STAFF_ANDROID_SYSTEM_PROMPT


# -----------------------------------------------------------------------------
# 3. Factory Fallback & Resolution Tests
# -----------------------------------------------------------------------------

def test_multimodal_client_factory_offline_fallbacks(monkeypatch):
    """Verifies factory returns MockMultimodalClient when environment keys are missing."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Default fallback
    default_client = get_multimodal_client()
    assert isinstance(default_client, MockMultimodalClient)

    # Explicit requests without credentials fallback to mock
    gemini_fallback = get_multimodal_client("gemini")
    assert isinstance(gemini_fallback, MockMultimodalClient)

    claude_fallback = get_multimodal_client("claude")
    assert isinstance(claude_fallback, MockMultimodalClient)

    openai_fallback = get_multimodal_client("openai")
    assert isinstance(openai_fallback, MockMultimodalClient)

    mock_explicit = get_multimodal_client("mock")
    assert isinstance(mock_explicit, MockMultimodalClient)


def test_multimodal_client_factory_with_keys(monkeypatch):
    """Verifies factory instantiates specific provider clients when credentials exist."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    gemini_client = get_multimodal_client("gemini")
    assert isinstance(gemini_client, GeminiMultimodalClient)
    assert gemini_client.api_key == "test-gemini-key"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    claude_client = get_multimodal_client("claude")
    assert isinstance(claude_client, ClaudeMultimodalClient)
    assert claude_client.api_key == "test-anthropic-key"

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    openai_client = get_multimodal_client("openai")
    assert isinstance(openai_client, OpenAIMultimodalClient)
    assert openai_client.api_key == "test-openai-key"


# -----------------------------------------------------------------------------
# 4. JSON Parser Resilience Tests
# -----------------------------------------------------------------------------

def test_json_extractor_resilience():
    """Validates extract_json handles markdown formatting, preambles, and malformed strings."""
    # 1. Clean JSON
    res1 = BaseMultimodalLLMClient.extract_json('{"key": "value"}')
    assert res1 == {"key": "value"}

    # 2. Markdown fenced JSON
    fenced_payload = "```json\n{\n  \"error_summary\": \"NPE in FeedScreen\",\n  \"confidence\": 0.95\n}\n```"
    res2 = BaseMultimodalLLMClient.extract_json(fenced_payload)
    assert res2["error_summary"] == "NPE in FeedScreen"

    # 3. Conversational wrapper with braces
    wrapper_payload = (
        "Here is the diagnostic report requested:\n\n"
        "```\n{\"status\": \"healthy\", \"count\": 42}\n```\n\n"
        "Let me know if you need more details!"
    )
    res3 = BaseMultimodalLLMClient.extract_json(wrapper_payload)
    assert res3 == {"status": "healthy", "count": 42}

    # 4. Non-JSON input raises ValueError
    with pytest.raises(ValueError):
        BaseMultimodalLLMClient.extract_json("This is purely plain text with no braces.")


# -----------------------------------------------------------------------------
# 5. Server Endpoints & Telemetry Integration Tests
# -----------------------------------------------------------------------------

def test_server_diagnose_api():
    """Tests /api/diagnose returns valid DiagnosticReport with usage metadata."""
    resp = client.post("/api/diagnose")
    assert resp.status_code == 200
    data = resp.json()

    assert "report_id" in data
    assert data["report_id"].startswith("RPT-")
    assert "error_summary" in data
    assert "root_cause_analysis" in data
    assert "confidence_score" in data
    assert "usage" in data
    assert data["usage"] is not None
    assert data["usage"]["provider"] in ["mock", "gemini", "claude", "openai"]
    assert data["usage"]["total_tokens"] > 0
    assert data["usage"]["latency_ms"] >= 0


def test_server_llm_status_and_select():
    """Tests /api/llm/status and /api/llm/select endpoints."""
    # 1. Check status
    r_status = client.get("/api/llm/status")
    assert r_status.status_code == 200
    status_data = r_status.json()
    assert "active_provider" in status_data
    assert "active_model" in status_data
    assert "available_providers" in status_data
    assert "cumulative_tokens" in status_data

    # 2. Switch provider via POST JSON
    r_sel_post = client.post("/api/llm/select", json={"provider": "mock", "model": "mock-test"})
    assert r_sel_post.status_code == 200
    post_data = r_sel_post.json()
    assert post_data["status"] == "success"
    assert post_data["active_provider"] == "mock"

    # 3. Switch provider via GET query
    r_sel_get = client.get("/api/llm/select?provider=mock")
    assert r_sel_get.status_code == 200
    assert r_sel_get.json()["active_provider"] == "mock"

    # 4. Bad request without provider
    r_bad = client.post("/api/llm/select", json={})
    assert r_bad.status_code == 400


# -----------------------------------------------------------------------------
# 6. Strict Privacy Boundary Verification
# -----------------------------------------------------------------------------

def test_privacy_compliance_check_diagnoser():
    """Verifies that no personal background details exist in newly created files."""
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

    files_to_check = [
        "src/core/llm_client.py",
        "src/prompts/templates.py",
        "tests/test_diagnoser.py",
    ]

    for filepath in files_to_check:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            for pattern in forbidden_patterns:
                match = pattern.search(content)
                assert match is None, f"Privacy violation in {filepath}: found '{match.group(0)}'"
