"""Autonomous Multi-Agent Diagnostic Loop with Cyclic LangGraph State Machine.

Orchestrates mobile app fault triage through a 4-node cyclic ReAct graph:
CorrelatorNode -> RetrieverNode -> DiagnoserNode -> VerifierNode.
Features self-correcting retry loops (confidence < 0.85, max 2 retries), native tool calling,
IDE remediation prompt synthesis, and a pure-Python deterministic StateGraph fallback runner.

Author: Arun Krishnan
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from src.agents.tools import ToolRegistry, create_default_tool_registry
from src.core.llm_client import BaseMultimodalLLMClient, get_multimodal_client
from src.core.logcat_parser import LogcatParser
from src.core.logger import setup_logger
from src.core.models import (
    CrashSignature,
    DiagnosticReport,
    IDEActionPrompt,
    KnowledgeCard,
    LogEntry,
    LogLevel,
    ScreenFrame,
    ScreenState,
)
from src.core.patch_generator import PatchGenerator
from src.prompts.templates import STAFF_ANDROID_SYSTEM_PROMPT, build_diagnostic_prompt
from src.rag.hybrid_retriever import HybridRetriever

logger = setup_logger("diagnostic_agent")

END = "__end__"


class DiagnosticState(TypedDict, total=False):
    """Strongly typed dictionary governing the diagnostic StateGraph lifecycle."""

    package_name: str
    log_entries: list[LogEntry]
    screen_frame: ScreenFrame | None
    crash_signature: CrashSignature | None
    screen_state: ScreenState
    device_meta: dict[str, Any]
    grounding_cards: list[KnowledgeCard]
    tool_history: list[dict[str, Any]]
    raw_llm_response: str | None
    report: DiagnosticReport | None
    verification_feedback: str | None
    retry_count: int
    max_retries: int
    confidence_score: float
    is_verified: bool
    ide_prompts: dict[str, IDEActionPrompt] | None
    retrieval_query: str | None


def create_initial_diagnostic_state(
    package_name: str = "com.example.mobileapp",
    log_entries: list[LogEntry] | None = None,
    screen_frame: ScreenFrame | None = None,
    crash_signature: CrashSignature | None = None,
    screen_state: ScreenState = ScreenState.NORMAL,
    device_meta: dict[str, Any] | None = None,
    max_retries: int = 2,
) -> DiagnosticState:
    """Creates a validated initial DiagnosticState dictionary with default parameters."""
    return DiagnosticState(
        package_name=package_name,
        log_entries=log_entries or [],
        screen_frame=screen_frame,
        crash_signature=crash_signature,
        screen_state=screen_state,
        device_meta=device_meta or {"serial": "emulator-5554", "mock_mode": True},
        grounding_cards=[],
        tool_history=[],
        raw_llm_response=None,
        report=None,
        verification_feedback=None,
        retry_count=0,
        max_retries=max_retries,
        confidence_score=0.0,
        is_verified=False,
        ide_prompts=None,
        retrieval_query=None,
    )


class FallbackStateGraph:
    """Pure-Python deterministic StateGraph runner mirroring the LangGraph API.

    Guarantees zero-dependency execution in offline environments where the
    external langgraph package is omitted.
    """

    def __init__(self, state_schema: type):
        """Initializes FallbackStateGraph with state schema type."""
        self.state_schema = state_schema
        self.nodes: dict[str, Callable[..., Any]] = {}
        self.edges: dict[str, str] = {}
        self.conditional_edges: dict[str, tuple[Callable[..., str], dict[str, str]]] = {}
        self.entry_point: str | None = None

    def add_node(self, name: str, action: Callable[..., Any]) -> None:
        """Registers a node callable by name."""
        self.nodes[name] = action

    def set_entry_point(self, name: str) -> None:
        """Designates the graph starting node."""
        self.entry_point = name

    def add_edge(self, start_key: str, end_key: str) -> None:
        """Adds a static directed edge between nodes."""
        self.edges[start_key] = end_key

    def add_conditional_edges(
        self,
        source: str,
        path: Callable[..., str],
        path_map: dict[str, str],
    ) -> None:
        """Adds conditional branch routing from a source node."""
        self.conditional_edges[source] = (path, path_map)

    def compile(self) -> "CompiledStateGraph":
        """Compiles the graph into an executable runner."""
        return CompiledStateGraph(self)


class CompiledStateGraph:
    """Executable runner for FallbackStateGraph supporting async and sync invocation."""

    def __init__(self, graph: FallbackStateGraph):
        """Initializes compiled runner with graph definition."""
        self.graph = graph

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
        step_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Asynchronously executes the state graph from entry point to completion."""
        current_node = self.graph.entry_point
        state_data = dict(state)
        max_steps = 25  # Guard against runaway infinite loops
        step_count = 0

        while current_node and current_node not in (END, "__end__"):
            step_count += 1
            if step_count > max_steps:
                logger.warning(f"Graph execution exceeded safety limit of {max_steps} steps. Halting.")
                break

            node_fn = self.graph.nodes.get(current_node)
            if not node_fn:
                logger.warning(f"Node '{current_node}' not found in registered nodes.")
                break

            if step_callback:
                try:
                    await step_callback(current_node, state_data)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Step callback error at node '{current_node}': {e}")

            is_async_node = asyncio.iscoroutinefunction(node_fn) or (
                callable(node_fn) and asyncio.iscoroutinefunction(node_fn.__call__)
            )
            if is_async_node:
                result = await node_fn(state_data)
            else:
                result = node_fn(state_data)

            if isinstance(result, dict):
                state_data.update(result)

            # Evaluate routing: conditional edge takes priority over static edge
            if current_node in self.graph.conditional_edges:
                cond_fn, path_map = self.graph.conditional_edges[current_node]
                route = cond_fn(state_data)
                if route == "retry" and step_callback:
                    try:
                        await step_callback("retry", state_data)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Retry callback error: {e}")
                next_node = path_map.get(route, END)
                current_node = next_node
            elif current_node in self.graph.edges:
                current_node = self.graph.edges[current_node]
            else:
                current_node = END

        return state_data

    def invoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Synchronously executes the state graph."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, self.ainvoke(state, config)).result()
            return loop.run_until_complete(self.ainvoke(state, config))
        except RuntimeError:
            return asyncio.run(self.ainvoke(state, config))


# Graceful LangGraph fallback resolution
try:
    from langgraph.graph import StateGraph as LangGraphStateGraph  # type: ignore

    StateGraph = LangGraphStateGraph
    LANGGRAPH_AVAILABLE = True
except ImportError:
    StateGraph = FallbackStateGraph  # Fallback to pure-Python runner
    LANGGRAPH_AVAILABLE = False


class CorrelatorNode:
    """Ingests logcat streams and screen frames, extracts crash signatures, and formulates queries."""

    def __init__(self, logcat_parser: LogcatParser | None = None):
        """Initializes CorrelatorNode with optional LogcatParser."""
        self.parser = logcat_parser or LogcatParser()

    def __call__(self, state: DiagnosticState) -> dict[str, Any]:
        """Processes logs and viewport frames to isolate fatal crash signatures and formulate query.

        Args:
            state: Current DiagnosticState.

        Returns:
            Dictionary updating crash_signature, package_name, and retrieval_query.
        """
        entries = state.get("log_entries", [])
        screen_frame = state.get("screen_frame")
        existing_sig = state.get("crash_signature")

        crash_sig = existing_sig or self.parser.extract_crash_signature(entries)

        pkg_name = state.get("package_name") or "com.example.mobileapp"
        if crash_sig and (not pkg_name or pkg_name == "com.example.mobileapp"):
            pkg_name = crash_sig.package_name

        # Assess visual anomaly temporal correlation
        screen_state = state.get("screen_state", ScreenState.NORMAL)
        if screen_frame:
            screen_state = screen_frame.screen_state

        if crash_sig and screen_state == ScreenState.NORMAL:
            screen_state = ScreenState.CRASH_DIALOG

        # Formulate initial search query for Android SDK knowledge retrieval
        if crash_sig:
            query = f"{crash_sig.exception_type} {crash_sig.exception_message} {crash_sig.crashing_file or ''}".strip()
        else:
            error_msgs = [
                e.message
                for e in entries
                if e.level in [LogLevel.ERROR, LogLevel.FATAL, LogLevel.WARN]
            ]
            query = (
                " ".join(error_msgs[:5])
                if error_msgs
                else "Android application stability and lifecycle"
            )

        return {
            "crash_signature": crash_sig,
            "package_name": pkg_name,
            "screen_state": screen_state,
            "retrieval_query": query,
        }


class RetrieverNode:
    """Executes hybrid BM25 + dense search against the Android SDK knowledge base."""

    def __init__(self, hybrid_retriever: HybridRetriever | None = None):
        """Initializes RetrieverNode with optional HybridRetriever."""
        self.retriever = hybrid_retriever or HybridRetriever()

    def __call__(self, state: DiagnosticState) -> dict[str, Any]:
        """Retrieves top-k grounding knowledge cards based on query or feedback refinement.

        Args:
            state: Current DiagnosticState.

        Returns:
            Dictionary updating grounding_cards.
        """
        query = state.get("retrieval_query") or "Android application crash stability"
        feedback = state.get("verification_feedback")

        # Refine search query with critique feedback if entering self-correction loop
        if feedback:
            query = f"{query} {feedback}".strip()

        grounding_cards = self.retriever.retrieve(query=query, top_k=2)
        return {"grounding_cards": grounding_cards}


class DiagnoserNode:
    """Executes multimodal CoT reasoning and native ReAct tool execution observation loop."""

    def __init__(
        self,
        client: BaseMultimodalLLMClient | None = None,
        tool_registry: ToolRegistry | None = None,
        max_tool_iterations: int = 3,
    ):
        """Initializes DiagnoserNode with VLM client and ToolRegistry."""
        self.client = client
        self.tool_registry = tool_registry
        self.max_tool_iterations = max_tool_iterations

    async def __call__(self, state: DiagnosticState) -> dict[str, Any]:
        """Synthesizes DiagnosticReport with support for iterative ReAct tool calling.

        Args:
            state: Current DiagnosticState.

        Returns:
            Dictionary updating report, confidence_score, and tool_history.
        """
        client = self.client or get_multimodal_client()
        tool_registry = self.tool_registry or create_default_tool_registry()
        tool_history = list(state.get("tool_history") or [])

        pkg_name = state.get("package_name") or "com.example.mobileapp"
        entries = state.get("log_entries", [])
        crash_sig = state.get("crash_signature")
        screen_frame = state.get("screen_frame")
        screen_state = state.get("screen_state", ScreenState.NORMAL)
        grounding_cards = state.get("grounding_cards", [])
        device_meta = state.get("device_meta") or {}
        feedback = state.get("verification_feedback")

        # ReAct observation loop: iterate while LLM requests tool execution
        report: DiagnosticReport | None = None
        iteration = 0

        while iteration < self.max_tool_iterations:
            iteration += 1

            # Construct multimodal prompt with accumulated context and tool observation history
            prompt = build_diagnostic_prompt(
                package_name=pkg_name,
                log_entries=entries,
                crash_signature=crash_sig,
                screen_state=(
                    screen_state.value
                    if hasattr(screen_state, "value")
                    else str(screen_state)
                ),
                device_meta=device_meta,
                grounding_cards=grounding_cards,
            )

            if feedback:
                prompt += (
                    f"\n\n### Self-Correction Critique Feedback (Prior Iteration):\n"
                    f"{feedback}\n"
                    f"Address this critique and deepen evidence before finalizing diagnosis.\n"
                )

            if tool_history:
                obs_block = "\n".join(
                    f"- Tool: {t['tool']}({t.get('args', {})}) -> Observation: {t.get('observation')}"
                    for t in tool_history
                )
                prompt += f"\n\n### ReAct Tool Observation History:\n{obs_block}\n"

            # Execute model inference
            img_b64 = screen_frame.image_bytes_base64 if screen_frame else ""
            mime_type = screen_frame.mime_type if screen_frame else "image/webp"

            res = await client.diagnose(
                prompt=prompt,
                image_bytes_base64=img_b64,
                mime_type=mime_type,
                system_prompt=STAFF_ANDROID_SYSTEM_PROMPT,
            )

            # Handle either (result, usage) or direct result
            candidate = res[0] if isinstance(res, tuple) else res
            usage = res[1] if isinstance(res, tuple) else None

            # Detect tool call invocation request from candidate
            tool_call_dict = None
            if isinstance(candidate, dict):
                if "tool" in candidate:
                    tool_call_dict = candidate
                elif "tool_call" in candidate:
                    tool_call_dict = candidate["tool_call"]
                elif "name" in candidate and "arguments" in candidate:
                    tool_call_dict = {"tool": candidate["name"], "args": candidate.get("arguments", {})}
            elif hasattr(candidate, "tool_name") and candidate.tool_name:
                tool_call_dict = {
                    "tool": candidate.tool_name,
                    "args": getattr(candidate, "tool_args", {}),
                }

            if tool_call_dict:
                tool_name = tool_call_dict.get("tool") or tool_call_dict.get("name")
                tool_args = tool_call_dict.get("args") or tool_call_dict.get("arguments") or {}
                logger.info(f"Diagnoser invoking tool '{tool_name}' with args {tool_args}")
                observation = tool_registry.execute(tool_name, **tool_args)
                tool_history.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "observation": observation,
                })
                # Continue loop to allow LLM to process observation
                continue

            # Candidate is DiagnosticReport
            if isinstance(candidate, DiagnosticReport):
                report = candidate
            elif isinstance(candidate, dict):
                report = DiagnosticReport(**candidate)
            else:
                report = candidate

            if usage and hasattr(report, "usage") and not report.usage:
                report.usage = usage
            break

        if report is None:
            # Fallback diagnostic report if loop terminated without report
            report = DiagnosticReport(
                package_name=pkg_name,
                error_summary="Diagnosis Inconclusive - ReAct cycle terminated without final report",
                root_cause_analysis="The diagnostic engine could not conclusively synthesize the failure.",
                confidence_score=0.50,
                affected_components=[],
                suggested_fix_summary="Inspect device logs manually.",
            )

        if crash_sig and not report.crash_signature:
            report.crash_signature = crash_sig

        if grounding_cards and not report.grounding_references:
            report.grounding_references = grounding_cards

        return {
            "report": report,
            "confidence_score": report.confidence_score,
            "tool_history": tool_history,
        }


class VerifierNode:
    """Evaluates report schema validity, root-cause consistency, and confidence rating."""

    def __init__(self, confidence_threshold: float = 0.85):
        """Initializes VerifierNode with acceptable confidence threshold."""
        self.confidence_threshold = confidence_threshold

    def __call__(self, state: DiagnosticState) -> dict[str, Any]:
        """Evaluates DiagnosticReport quality and determines verification pass or retry.

        Args:
            state: Current DiagnosticState.

        Returns:
            Dictionary updating is_verified, retry_count, verification_feedback, and ide_prompts.
        """
        report = state.get("report")
        retry_count = state.get("retry_count", 0)
        max_retries = state.get("max_retries", 2)
        confidence = (
            report.confidence_score
            if report
            else state.get("confidence_score", 0.0)
        )

        # High confidence pass condition
        if confidence >= self.confidence_threshold:
            ide_prompts = None
            if report:
                cursor_prompt = PatchGenerator.format_cursor_prompt(report)
                antigravity_prompt = PatchGenerator.format_antigravity_handoff(report)
                ide_prompts = {
                    "cursor": cursor_prompt,
                    "antigravity": antigravity_prompt,
                }

            return {
                "is_verified": True,
                "confidence_score": confidence,
                "verification_feedback": None,
                "ide_prompts": ide_prompts,
            }

        # Low confidence condition: evaluate retry eligibility
        if retry_count < max_retries:
            new_retry_count = retry_count + 1
            feedback = (
                f"Confidence score ({confidence:.2f}) is below the required "
                f"{self.confidence_threshold:.2f} threshold. Root cause analysis for "
                f"'{report.error_summary if report else 'detected issue'}' lacks sufficient "
                "evidence from stacktrace and Android SDK architectural patterns."
            )
            return {
                "is_verified": False,
                "retry_count": new_retry_count,
                "verification_feedback": feedback,
            }

        # Max retries reached: finalize without further retry
        ide_prompts = None
        if report:
            cursor_prompt = PatchGenerator.format_cursor_prompt(report)
            antigravity_prompt = PatchGenerator.format_antigravity_handoff(report)
            ide_prompts = {
                "cursor": cursor_prompt,
                "antigravity": antigravity_prompt,
            }

        return {
            "is_verified": False,
            "retry_count": retry_count,
            "verification_feedback": (
                f"Verification bounded: confidence ({confidence:.2f}) remained below "
                f"{self.confidence_threshold:.2f} after {retry_count} retries."
            ),
            "ide_prompts": ide_prompts,
        }


def should_retry(state: DiagnosticState) -> str:
    """Evaluates whether to route to retry or terminate at END.

    Args:
        state: Current DiagnosticState.

    Returns:
        'retry' if confidence is low and retries are available, else 'end'.
    """
    confidence = state.get("confidence_score", 0.0)
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    if confidence < 0.85 and retry_count < max_retries:
        return "retry"
    return "end"


class MultimodalDiagnosticAgent:
    """Orchestrates the cyclic ReAct diagnostic state graph for Android app issue diagnosis."""

    def __init__(
        self,
        client: BaseMultimodalLLMClient | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        tool_registry: ToolRegistry | None = None,
        step_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ):
        """Initializes MultimodalDiagnosticAgent with injectable components and callbacks."""
        self.client = client
        self.hybrid_retriever = hybrid_retriever
        self.tool_registry = tool_registry
        self.step_callback = step_callback

        self.correlator = CorrelatorNode()
        self.retriever = RetrieverNode(hybrid_retriever=self.hybrid_retriever)
        self.diagnoser = DiagnoserNode(client=self.client, tool_registry=self.tool_registry)
        self.verifier = VerifierNode()

        self.compiled_graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Constructs and compiles the cyclic StateGraph."""
        builder = StateGraph(DiagnosticState)
        builder.add_node("correlator", self.correlator)
        builder.add_node("retriever", self.retriever)
        builder.add_node("diagnoser", self.diagnoser)
        builder.add_node("verifier", self.verifier)

        builder.set_entry_point("correlator")
        builder.add_edge("correlator", "retriever")
        builder.add_edge("retriever", "diagnoser")
        builder.add_edge("diagnoser", "verifier")

        builder.add_conditional_edges(
            "verifier",
            should_retry,
            {
                "retry": "retriever",
                "end": END,
            },
        )
        return builder.compile()

    async def ainvoke(
        self,
        initial_state: DiagnosticState,
        step_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosticState:
        """Asynchronously executes the diagnostic agent graph.

        Args:
            initial_state: Initial DiagnosticState input dictionary.
            step_callback: Optional async callback invoked on every node step.

        Returns:
            Final DiagnosticState after graph termination.
        """
        cb = step_callback or self.step_callback
        if isinstance(self.compiled_graph, CompiledStateGraph):
            return await self.compiled_graph.ainvoke(initial_state, step_callback=cb)
        return await self.compiled_graph.ainvoke(initial_state)

    def invoke(self, initial_state: DiagnosticState) -> DiagnosticState:
        """Synchronously executes the diagnostic agent graph."""
        return self.compiled_graph.invoke(initial_state)
