"""Comprehensive Test Suite for Autonomous Multi-Agent Diagnostic Loop with LangGraph (AGENT-01).

Implements all 25 test cases from .ai/context/test-strategies/AGENT-01.md:
- L1 Tools Layer (Unit): DeviceInfoTool, LogFilterTool, FileInspectionTool, ToolRegistry
- L2 StateGraph & Nodes (Unit): DiagnosticState, CorrelatorNode, RetrieverNode, DiagnoserNode, VerifierNode
- L3 Self-Correction Loop: should_retry conditional edge, max retries bounding, fallback runner
- L4 IDE Remediation Bridge: PatchGenerator integration with Cursor Composer and Antigravity MAS
- L5 API & Telemetry (Integration): FastAPI /api/diagnose, WebSocket step broadcasting
- Privacy Compliance: Strict zero-leakage gate

Author: Arun Krishnan
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.server import app, manager
from src.agents.diagnostic_agent import (
    CorrelatorNode,
    DiagnoserNode,
    DiagnosticState,
    FallbackStateGraph,
    MultimodalDiagnosticAgent,
    RetrieverNode,
    VerifierNode,
    create_initial_diagnostic_state,
    should_retry,
)
from src.agents.tools import (
    DeviceInfoTool,
    FileInspectionTool,
    LogFilterTool,
    ToolRegistry,
    create_default_tool_registry,
)
from src.core.adb_manager import ADBManager
from src.core.llm_client import (
    BaseMultimodalLLMClient,
    MockMultimodalClient,
)
from src.core.models import (
    DiagnosticReport,
    KnowledgeCard,
    LLMUsageMetadata,
    LogEntry,
    LogLevel,
    ScreenFrame,
    ScreenState,
)
from src.core.patch_generator import PatchGenerator
from src.rag.hybrid_retriever import HybridRetriever

client = TestClient(app)

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


# -----------------------------------------------------------------------------
# Fixtures & Helpers
# -----------------------------------------------------------------------------

@pytest.fixture
def sample_log_entries() -> list[LogEntry]:
    """Generates synthetic in-memory logcat entries representing a fatal crash."""
    return [
        LogEntry(
            timestamp="11-15 12:00:01.100",
            pid=2345,
            tid=2345,
            level=LogLevel.INFO,
            tag="ActivityManager",
            message="Start proc 2345:com.example.mobileapp/u0a120 for activity",
        ),
        LogEntry(
            timestamp="11-15 12:00:01.200",
            pid=2345,
            tid=2345,
            level=LogLevel.DEBUG,
            tag="FeedScreen",
            message="Rendering feed items for user profile",
        ),
        LogEntry(
            timestamp="11-15 12:00:01.300",
            pid=2345,
            tid=2345,
            level=LogLevel.WARN,
            tag="FeedScreen",
            message="Slow frame render detected: 32ms frame time",
        ),
        LogEntry(
            timestamp="11-15 12:00:02.000",
            pid=2345,
            tid=2345,
            level=LogLevel.ERROR,
            tag="AndroidRuntime",
            message="FATAL EXCEPTION: main",
            is_stacktrace=True,
        ),
        LogEntry(
            timestamp="11-15 12:00:02.001",
            pid=2345,
            tid=2345,
            level=LogLevel.ERROR,
            tag="AndroidRuntime",
            message="Process: com.example.mobileapp, PID: 2345",
            is_stacktrace=True,
        ),
        LogEntry(
            timestamp="11-15 12:00:02.002",
            pid=2345,
            tid=2345,
            level=LogLevel.ERROR,
            tag="AndroidRuntime",
            message="java.lang.NullPointerException: Attempt to invoke virtual method 'java.lang.String com.example.mobileapp.UserProfile.getName()' on a null object reference",
            is_stacktrace=True,
        ),
        LogEntry(
            timestamp="11-15 12:00:02.003",
            pid=2345,
            tid=2345,
            level=LogLevel.ERROR,
            tag="AndroidRuntime",
            message="\tat com.example.mobileapp.ui.FeedScreenKt.FeedScreen(FeedScreen.kt:42)",
            is_stacktrace=True,
        ),
    ]


@pytest.fixture
def sample_screen_frame() -> ScreenFrame:
    """Provides a synthetic crash dialog ScreenFrame."""
    return ScreenFrame(
        frame_id="frame-test-01",
        timestamp="2026-11-16T12:00:02Z",
        image_bytes_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
        mime_type="image/png",
        width=720,
        height=1280,
        screen_state=ScreenState.CRASH_DIALOG,
    )


# -----------------------------------------------------------------------------
# 1. DeviceInfoTool Unit Tests (Cases 1 & 2)
# -----------------------------------------------------------------------------

def test_device_info_tool():
    """Case 1: Execute with mock ADB manager returning valid device properties."""
    mock_adb = ADBManager(mock_mode=True)
    tool = DeviceInfoTool(adb_manager=mock_adb)

    result = tool.execute()
    assert result["status"] == "success"
    assert "model" in result
    assert result["model"] == "Pixel_8_Pro_Virtual"
    assert result["api_level"] == 34
    assert result["abi"] == "arm64-v8a"
    assert "display_metrics" in result
    assert result["display_metrics"]["width"] == 1080
    assert result["display_metrics"]["height"] == 2400
    assert result["serial"] == "emulator-5554"


def test_device_info_tool_error():
    """Case 2: Execute when ADB manager throws exception; fallback returns structured error dict."""
    broken_adb = MagicMock()
    broken_adb.mock_mode = False
    broken_adb.list_devices.side_effect = RuntimeError("ADB daemon unresponsive or connection refused")

    tool = DeviceInfoTool(adb_manager=broken_adb)
    result = tool.execute()

    assert result["status"] == "error"
    assert "error" in result
    assert "unresponsive" in result["error"] or "unresponsive" in result["message"]


# -----------------------------------------------------------------------------
# 2. LogFilterTool Unit Tests (Cases 3 & 4)
# -----------------------------------------------------------------------------

def test_log_filter_tool(sample_log_entries):
    """Case 3: Filter logcat entries by tag='AndroidRuntime', level=LogLevel.ERROR, and regex pattern."""
    tool = LogFilterTool(log_entries=sample_log_entries)

    result = tool.execute(
        tag="AndroidRuntime",
        level=LogLevel.ERROR,
        pattern=r"NullPointerException",
    )

    assert result["status"] == "success"
    assert result["total_matches"] == 1
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry.tag == "AndroidRuntime"
    assert entry.level == LogLevel.ERROR
    assert "NullPointerException" in entry.message


def test_log_filter_empty(sample_log_entries):
    """Case 4: Filter with non-matching criteria returns clean empty list and descriptive summary."""
    tool = LogFilterTool(log_entries=sample_log_entries)

    result = tool.execute(tag="NonExistentTagXYZ")

    assert result["status"] == "empty"
    assert result["total_matches"] == 0
    assert result["entries"] == []
    assert "summary" in result
    assert "No log entries matched" in result["summary"]


# -----------------------------------------------------------------------------
# 3. FileInspectionTool Unit Tests & Security Traversal (Cases 5, 6, 7)
# -----------------------------------------------------------------------------

def test_file_inspection_success():
    """Case 5: Inspect valid source code file within workspace, reading specified line window."""
    tool = FileInspectionTool(workspace_root=WORKSPACE_ROOT)

    result = tool.execute(
        file_path="src/agents/tools.py",
        start_line=1,
        end_line=30,
    )

    assert result["status"] == "success"
    assert result["file_path"] == "src/agents/tools.py"
    assert result["start_line"] == 1
    assert result["end_line"] == 30
    assert "class Tool" in result["content"]
    assert "1: " in result["numbered_content"]


def test_file_inspection_security_traversal():
    """Case 6: Attempt directory traversal via relative '..' or absolute path outside workspace."""
    tool = FileInspectionTool(workspace_root=WORKSPACE_ROOT)

    # Relative path traversal attack
    res_rel = tool.execute(file_path="../../windows/system32/cmd.exe")
    assert res_rel["status"] == "error"
    assert "SecurityViolation" in res_rel["error"]

    # Absolute path outside workspace root
    res_abs = tool.execute(file_path="C:/Windows/System32/drivers/etc/hosts")
    assert res_abs["status"] == "error"
    assert "SecurityViolation" in res_abs["error"]


def test_file_inspection_missing_file():
    """Case 7: Non-existent file path returns structured error message without unhandled exception."""
    tool = FileInspectionTool(workspace_root=WORKSPACE_ROOT)

    result = tool.execute(file_path="src/non_existent_folder/FakeFile.kt")

    assert result["status"] == "error"
    assert "FileNotFound" in result["error"]
    assert "does not exist on disk" in result["message"]


# -----------------------------------------------------------------------------
# 4. ToolRegistry Unit Tests (Cases 8 & 9)
# -----------------------------------------------------------------------------

def test_tool_registry_schemas():
    """Case 8: Register tools and export function calling schema compatible with LLM tool calling format."""
    empty_reg = ToolRegistry()
    assert empty_reg.list_tools() == []

    registry = create_default_tool_registry(workspace_root=WORKSPACE_ROOT)
    schemas = registry.get_schemas()

    assert len(schemas) >= 3
    tool_names = [s["function"]["name"] for s in schemas]
    assert "device_info" in tool_names
    assert "log_filter" in tool_names
    assert "file_inspection" in tool_names

    for schema in schemas:
        assert schema["type"] == "function"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]
        assert schema["function"]["parameters"]["type"] == "object"


def test_tool_registry_dispatch():
    """Case 9: Execute registered tool by name with kwargs, and handle unknown tool name gracefully."""
    registry = create_default_tool_registry(workspace_root=WORKSPACE_ROOT)

    # Valid tool execution
    dev_res = registry.execute("device_info")
    assert dev_res["status"] == "success"

    # Unknown tool dispatch
    unknown_res = registry.execute("unknown_cyber_tool", param=123)
    assert unknown_res["status"] == "error"
    assert "ToolNotFound" in unknown_res["error"]


# -----------------------------------------------------------------------------
# 5. DiagnosticState & Node Tests (Cases 10, 11, 12, 13, 14, 15)
# -----------------------------------------------------------------------------

def test_diagnostic_state_schema():
    """Case 10: Validate state instantiation with defaults, required fields, and type preservation."""
    state = create_initial_diagnostic_state(
        package_name="com.example.mobileapp",
        max_retries=2,
    )

    assert state["package_name"] == "com.example.mobileapp"
    assert state["log_entries"] == []
    assert state["screen_frame"] is None
    assert state["crash_signature"] is None
    assert state["retry_count"] == 0
    assert state["max_retries"] == 2
    assert state["confidence_score"] == 0.0
    assert state["is_verified"] is False
    assert state["tool_history"] == []
    assert state["grounding_cards"] == []


def test_correlator_node(sample_log_entries, sample_screen_frame):
    """Case 11: Ingest log entries with FATAL EXCEPTION and screen frame; extract CrashSignature and query."""
    node = CorrelatorNode()
    state = create_initial_diagnostic_state(
        package_name="com.example.mobileapp",
        log_entries=sample_log_entries,
        screen_frame=sample_screen_frame,
    )

    result = node(state)

    assert result["crash_signature"] is not None
    assert result["crash_signature"].exception_type == "java.lang.NullPointerException"
    assert result["crash_signature"].crashing_file == "FeedScreen.kt"
    assert result["crash_signature"].crashing_line == 42
    assert "NullPointerException" in result["retrieval_query"]
    assert "FeedScreen.kt" in result["retrieval_query"]
    assert result["screen_state"] == ScreenState.CRASH_DIALOG


def test_retriever_node():
    """Case 12: Execute query against HybridRetriever; verify state updated with top-k grounding_cards."""
    retriever = HybridRetriever()
    node = RetrieverNode(hybrid_retriever=retriever)
    state = create_initial_diagnostic_state(
        package_name="com.example.mobileapp",
    )
    state["retrieval_query"] = "NullPointerException Composable lifecycle state collectAsState"

    result = node(state)

    assert "grounding_cards" in result
    cards = result["grounding_cards"]
    assert len(cards) > 0
    assert isinstance(cards[0], KnowledgeCard)
    assert any("COMPOSE" in c.category or "LIFECYCLE" in c.category for c in cards)


@pytest.mark.asyncio
async def test_diagnoser_node_tool_calling(sample_log_entries, sample_screen_frame):
    """Case 13: Execute ReAct tool calling cycle: LLM generates tool call, node executes tool, appends observation, synthesizes report."""
    # Deterministic mock VLM returning a tool call first, then the final report
    class MockReActVLMClient(BaseMultimodalLLMClient):
        def __init__(self):
            super().__init__(model="mock-react", provider="mock")
            self.call_count = 0

        async def diagnose(self, *args, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                # LLM decides it needs hardware context before finalizing diagnosis
                return {"tool": "device_info", "args": {}}, LLMUsageMetadata(
                    provider="mock",
                    model_name="mock-react",
                    total_tokens=45,
                )
            # Second call: LLM uses tool observation to finalize DiagnosticReport
            rep = DiagnosticReport(
                package_name="com.example.mobileapp",
                error_summary="FATAL EXCEPTION: NullPointerException in FeedScreen.kt:42",
                root_cause_analysis="Null dereference in FeedScreen on device Pixel_8_Pro_Virtual.",
                confidence_score=0.94,
                affected_components=["app/src/main/java/com/example/ui/FeedScreen.kt"],
                suggested_fix_summary="Apply safe navigation (?.) operator.",
            )
            return rep, LLMUsageMetadata(
                provider="mock",
                model_name="mock-react",
                total_tokens=85,
            )

    client = MockReActVLMClient()
    registry = create_default_tool_registry(workspace_root=WORKSPACE_ROOT)
    diagnoser = DiagnoserNode(client=client, tool_registry=registry)

    state = create_initial_diagnostic_state(
        package_name="com.example.mobileapp",
        log_entries=sample_log_entries,
        screen_frame=sample_screen_frame,
    )

    result = await diagnoser(state)

    assert len(result["tool_history"]) == 1
    assert result["tool_history"][0]["tool"] == "device_info"
    assert result["tool_history"][0]["observation"]["status"] == "success"
    assert result["report"] is not None
    assert result["report"].confidence_score == 0.94
    assert result["confidence_score"] == 0.94


def test_verifier_node_success():
    """Case 14: Evaluate valid high-confidence report (confidence >= 0.85); marks is_verified=True and generates ide_prompts."""
    verifier = VerifierNode(confidence_threshold=0.85)
    report = DiagnosticReport(
        package_name="com.example.mobileapp",
        error_summary="NullPointerException resolved",
        root_cause_analysis="Valid cause analysis confirmed with stacktrace.",
        confidence_score=0.92,
        suggested_fix_summary="Use safe navigation operator.",
    )
    state = create_initial_diagnostic_state(package_name="com.example.mobileapp")
    state["report"] = report
    state["confidence_score"] = 0.92

    result = verifier(state)

    assert result["is_verified"] is True
    assert result["confidence_score"] == 0.92
    assert result["verification_feedback"] is None
    assert result["ide_prompts"] is not None
    assert "cursor" in result["ide_prompts"]
    assert "antigravity" in result["ide_prompts"]


def test_verifier_node_critique():
    """Case 15: Evaluate low-confidence report (confidence < 0.85); increments retry_count, generates verification_feedback."""
    verifier = VerifierNode(confidence_threshold=0.85)
    report = DiagnosticReport(
        package_name="com.example.mobileapp",
        error_summary="Uncertain exception in unknown component",
        root_cause_analysis="Evidence is ambiguous.",
        confidence_score=0.70,
        suggested_fix_summary="Inspect manually.",
    )
    state = create_initial_diagnostic_state(package_name="com.example.mobileapp")
    state["report"] = report
    state["confidence_score"] = 0.70
    state["retry_count"] = 0
    state["max_retries"] = 2

    result = verifier(state)

    assert result["is_verified"] is False
    assert result["retry_count"] == 1
    assert result["verification_feedback"] is not None
    assert "0.70" in result["verification_feedback"] or "0.85" in result["verification_feedback"]


# -----------------------------------------------------------------------------
# 6. Self-Correction & Graph Execution Tests (Cases 16, 17, 18, 19, 20)
# -----------------------------------------------------------------------------

def test_state_graph_retry_branch():
    """Case 16: Verify conditional routing: confidence < 0.85 and retries < 2 routes to 'retry'."""
    state1 = DiagnosticState(confidence_score=0.72, retry_count=0, max_retries=2)
    assert should_retry(state1) == "retry"

    state2 = DiagnosticState(confidence_score=0.72, retry_count=1, max_retries=2)
    assert should_retry(state2) == "retry"


def test_state_graph_max_retries_termination():
    """Case 17: Verify retry bounding: confidence < 0.85 and retries >= 2 transitions to 'end' without infinite loop."""
    # Retries reached threshold
    state_bounded = DiagnosticState(confidence_score=0.72, retry_count=2, max_retries=2)
    assert should_retry(state_bounded) == "end"

    # High confidence also transitions to end
    state_high_conf = DiagnosticState(confidence_score=0.91, retry_count=0, max_retries=2)
    assert should_retry(state_high_conf) == "end"


def test_fallback_state_graph_runner():
    """Case 18: Run state graph with pure-Python fallback runner without langgraph package dependency."""
    builder = FallbackStateGraph(DiagnosticState)

    def node_inc(state: dict) -> dict:
        return {"val": state.get("val", 0) + 1}

    def node_double(state: dict) -> dict:
        return {"val": state.get("val", 0) * 2}

    builder.add_node("inc", node_inc)
    builder.add_node("double", node_double)
    builder.set_entry_point("inc")
    builder.add_edge("inc", "double")

    graph = builder.compile()
    output = graph.invoke({"val": 5})

    assert output["val"] == 12  # (5 + 1) * 2 = 12


@pytest.mark.asyncio
async def test_e2e_agent_graph_crash(sample_log_entries, sample_screen_frame):
    """Case 19: Run complete graph for fatal NPE crash: logs + frame -> Correlator -> Retriever -> Diagnoser -> Verifier -> IDE Prompts."""
    mock_client = MockMultimodalClient()
    agent = MultimodalDiagnosticAgent(
        client=mock_client,
        hybrid_retriever=HybridRetriever(),
        tool_registry=create_default_tool_registry(workspace_root=WORKSPACE_ROOT),
    )

    initial_state = create_initial_diagnostic_state(
        package_name="com.example.mobileapp",
        log_entries=sample_log_entries,
        screen_frame=sample_screen_frame,
    )

    final_state = await agent.ainvoke(initial_state)

    assert final_state["is_verified"] is True
    assert final_state["report"] is not None
    assert final_state["report"].confidence_score >= 0.85
    assert final_state["crash_signature"] is not None
    assert final_state["crash_signature"].exception_type == "java.lang.NullPointerException"
    assert len(final_state["grounding_cards"]) > 0
    assert final_state["ide_prompts"] is not None
    assert "cursor" in final_state["ide_prompts"]
    assert "antigravity" in final_state["ide_prompts"]


@pytest.mark.asyncio
async def test_e2e_agent_graph_self_correction(sample_log_entries, sample_screen_frame):
    """Case 20: Run complete graph where iteration 1 yields confidence 0.75, self-correction triggers, iteration 2 yields 0.92 and succeeds."""
    class SelfCorrectingVLMClient(BaseMultimodalLLMClient):
        def __init__(self):
            super().__init__(model="self-correct", provider="mock")
            self.iteration = 0

        async def diagnose(self, *args, **kwargs):
            self.iteration += 1
            if self.iteration == 1:
                # Iteration 1: Low confidence diagnosis
                return DiagnosticReport(
                    package_name="com.example.mobileapp",
                    error_summary="Tentative Crash Finding",
                    root_cause_analysis="Initial hypothesis needs verification against grounding cards.",
                    confidence_score=0.75,
                    suggested_fix_summary="Check null safety.",
                ), LLMUsageMetadata(provider="mock", model_name="self-correct", total_tokens=60)

            # Iteration 2: Grounded, high confidence diagnosis incorporating critique
            return DiagnosticReport(
                package_name="com.example.mobileapp",
                error_summary="FATAL EXCEPTION: java.lang.NullPointerException in FeedScreen.kt:42",
                root_cause_analysis="Verified root cause: uiState.userProfile dereferenced before loading completed.",
                confidence_score=0.92,
                suggested_fix_summary="Use safe call operator (?.) or default value.",
            ), LLMUsageMetadata(provider="mock", model_name="self-correct", total_tokens=90)

    client = SelfCorrectingVLMClient()
    agent = MultimodalDiagnosticAgent(
        client=client,
        hybrid_retriever=HybridRetriever(),
    )

    initial_state = create_initial_diagnostic_state(
        package_name="com.example.mobileapp",
        log_entries=sample_log_entries,
        screen_frame=sample_screen_frame,
    )

    final_state = await agent.ainvoke(initial_state)

    assert final_state["retry_count"] == 1
    assert final_state["is_verified"] is True
    assert final_state["report"].confidence_score == 0.92
    assert final_state["ide_prompts"] is not None


# -----------------------------------------------------------------------------
# 7. IDE Remediation Bridge Unit Tests (Cases 21 & 22)
# -----------------------------------------------------------------------------

def test_patch_generator_cursor_integration():
    """Case 21: Verify Cursor Composer prompt synthesis integrates DiagnosticReport and FileInspectionTool source snippets."""
    report = DiagnosticReport(
        package_name="com.example.mobileapp",
        error_summary="NullPointerException in FeedScreen.kt:42",
        root_cause_analysis="Null dereference on uiState.userProfile",
        confidence_score=0.95,
        affected_components=["app/src/main/java/com/example/ui/FeedScreen.kt"],
        suggested_fix_summary="Add safe-call operator (?.)",
    )

    inspection_result = {
        "status": "success",
        "file_path": "app/src/main/java/com/example/ui/FeedScreen.kt",
        "start_line": 35,
        "end_line": 50,
        "content": "Text(text = uiState.userProfile.name)\n",
    }

    cursor_prompt = PatchGenerator.generate_cursor_prompt(
        report=report,
        file_inspection_result=inspection_result,
        modified_code="Text(text = uiState.userProfile?.name ?: \"Guest\")\n",
    )

    assert cursor_prompt.target_ide == "cursor"
    assert "@app/src/main/java/com/example/ui/FeedScreen.kt" in cursor_prompt.formatted_prompt
    assert "Contextual Source Code" in cursor_prompt.formatted_prompt
    assert "Text(text = uiState.userProfile.name)" in cursor_prompt.formatted_prompt
    assert "```diff" in cursor_prompt.formatted_prompt


def test_patch_generator_antigravity_integration():
    """Case 22: Verify Antigravity MAS handoff synthesis formats structured role, task id, files, and diff."""
    report = DiagnosticReport(
        report_id="RPT-AGENT01",
        package_name="com.example.mobileapp",
        error_summary="NullPointerException in FeedScreen.kt:42",
        root_cause_analysis="Null dereference on uiState.userProfile",
        confidence_score=0.96,
        affected_components=["app/src/main/java/com/example/ui/FeedScreen.kt"],
        suggested_fix_summary="Add safe-call operator (?.)",
    )

    handoff = PatchGenerator.generate_antigravity_handoff(
        report=report,
        target_file="app/src/main/java/com/example/ui/FeedScreen.kt",
        original_code="Text(text = uiState.userProfile.name)\n",
        modified_code="Text(text = uiState.userProfile?.name ?: \"Guest\")\n",
        task_id="FIX-AGENT01",
    )

    assert handoff.target_ide == "antigravity"
    assert "ROLE: general-dev" in handoff.formatted_prompt
    assert "TASK_ID: FIX-AGENT01" in handoff.formatted_prompt
    assert "FILES:\n* app/src/main/java/com/example/ui/FeedScreen.kt" in handoff.formatted_prompt
    assert "DIFF:" in handoff.formatted_prompt
    assert "DONE" in handoff.formatted_prompt


# -----------------------------------------------------------------------------
# 8. API & WebSocket Telemetry Integration Tests (Cases 23 & 24)
# -----------------------------------------------------------------------------

def test_api_diagnose_agent_graph():
    """Case 23: POST /api/diagnose invokes agent graph asynchronously and returns validated DiagnosticReport."""
    resp = client.post("/api/diagnose")
    assert resp.status_code == 200

    data = resp.json()
    assert "report_id" in data
    assert data["report_id"].startswith("RPT-")
    assert "error_summary" in data
    assert "root_cause_analysis" in data
    assert "confidence_score" in data
    assert data["confidence_score"] >= 0.85
    assert "usage" in data
    assert data["usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_api_diagnose_ws_telemetry():
    """Case 24: Verify step events ('correlator', 'retriever', 'diagnoser', 'verifier') are broadcast during diagnose execution."""
    broadcast_events: list[dict] = []

    async def mock_broadcast(msg: dict):
        broadcast_events.append(msg)

    with patch.object(manager, "broadcast_control", side_effect=mock_broadcast):
        resp = client.post("/api/diagnose")
        assert resp.status_code == 200

    step_types = [e.get("step") for e in broadcast_events if e.get("type") == "agent_step"]
    assert "correlator" in step_types
    assert "retriever" in step_types
    assert "diagnoser" in step_types
    assert "verifier" in step_types


# -----------------------------------------------------------------------------
# 9. Privacy Compliance Gate (Case 25)
# -----------------------------------------------------------------------------

def test_agent_graph_privacy_compliance():
    """Case 25: Scan all modified files and module code to assert zero leakage of prohibited personal info."""
    forbidden_tokens = [
        "cisco",
        "webex",
        "intel",
        "qualcomm",
        "samsung",
        "nvidia",
        "university",
        "iit",
        "bangalore",
        "bengaluru",
        "karnataka",
        "india",
        "california",
        "san jose",
        "rtx 4090",
        "rtx 3080",
    ]

    files_to_scan = [
        WORKSPACE_ROOT / "src/agents/tools.py",
        WORKSPACE_ROOT / "src/agents/diagnostic_agent.py",
        WORKSPACE_ROOT / "src/core/patch_generator.py",
        WORKSPACE_ROOT / "apps/server.py",
    ]

    for file_path in files_to_scan:
        content = file_path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            pattern = rf"\b{re.escape(token)}\b"
            assert not re.search(pattern, content), (
                f"Privacy violation: Prohibited token '{token}' discovered in {file_path.name}"
            )

        # Confirm sole allowed author attribution
        if "author:" in content:
            assert "arun krishnan" in content, (
                f"Missing or mismatched author attribution in {file_path.name}"
            )
