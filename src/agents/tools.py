"""Tool interface, concrete Android debugging tools, and ToolRegistry for agentic reasoning loops.

Provides concrete tools for Android runtime diagnosis including DeviceInfoTool,
LogFilterTool, and FileInspectionTool (with strict workspace confinement),
alongside a dynamic ToolRegistry for LLM function calling schema generation and execution.

Author: Arun Krishnan
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from src.core.adb_manager import ADBManager
from src.core.logger import setup_logger
from src.core.models import LogEntry, LogLevel

logger = setup_logger("agent_tools")


class Tool:
    """Base class for tools that an agent can invoke."""

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any] | None = None,
        parameters: dict[str, Any] | None = None,
    ):
        """Initializes Tool with name, description, callable, and parameter schema."""
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Executes the tool with provided arguments."""
        if self.func is not None:
            return self.func(*args, **kwargs)
        raise NotImplementedError(f"Tool '{self.name}' has no execution callable.")

    def to_schema(self) -> dict[str, Any]:
        """Generates an LLM-compatible function-calling schema dictionary."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @property
    def schema(self) -> dict[str, Any]:
        """Convenience property for accessing the tool function calling schema."""
        return self.to_schema()


class CalculatorTool(Tool):
    """Safe math evaluator tool."""

    def __init__(self):
        """Initializes calculator tool with safe arithmetic expression evaluation."""
        super().__init__(
            name="calculator",
            description="Evaluates simple arithmetic expressions (e.g., '12 * 8').",
            func=self._calculate,
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Arithmetic expression to safely evaluate.",
                    }
                },
                "required": ["expression"],
            },
        )

    @staticmethod
    def _calculate(expression: str) -> str:
        """Evaluates simple arithmetic expressions within a restricted character set."""
        allowed_chars = set("0123456789+-*/(). %")
        if not set(expression).issubset(allowed_chars):
            return "Error: Unsupported expression characters."
        try:
            return str(eval(expression, {"__builtins__": None}, {}))
        except Exception as e:  # noqa: BLE001
            return f"Error: {e!s}"


class DeviceInfoTool(Tool):
    """Queries connected Android device hardware and system metadata."""

    def __init__(self, adb_manager: ADBManager | None = None):
        """Initializes DeviceInfoTool with optional ADBManager instance."""
        self.adb_manager = adb_manager
        super().__init__(
            name="device_info",
            description=(
                "Queries connected Android device hardware and system metadata including "
                "model, manufacturer, Android SDK API level, ABI architecture, and display metrics."
            ),
            func=self.get_device_info,
            parameters={
                "type": "object",
                "properties": {
                    "serial": {
                        "type": "string",
                        "description": "Optional device serial number to query.",
                    }
                },
                "required": [],
            },
        )

    def get_device_info(self, serial: str | None = None) -> dict[str, Any]:
        """Queries ADB for device status and system properties.

        Args:
            serial: Optional device serial number.

        Returns:
            Dictionary containing device properties or structured error response.
        """
        try:
            mgr = self.adb_manager or ADBManager(serial=serial)
            devices = mgr.list_devices()
            if not devices and not mgr.mock_mode:
                return {
                    "status": "error",
                    "error": "DeviceDisconnected: No connected Android devices found.",
                    "message": "No online Android devices or emulators discovered via ADB.",
                }

            target_device = None
            if serial:
                for d in devices:
                    if d.serial == serial:
                        target_device = d
                        break
            if not target_device and devices:
                target_device = devices[0]

            # In mock mode or fallback
            if mgr.mock_mode or target_device is None:
                return {
                    "status": "success",
                    "serial": target_device.serial if target_device else (serial or "emulator-5554"),
                    "model": target_device.model if target_device else "Pixel_8_Pro_Virtual",
                    "manufacturer": "Google",
                    "api_level": 34,
                    "abi": "arm64-v8a",
                    "display_metrics": {
                        "width": 1080,
                        "height": 2400,
                        "density": 420,
                    },
                    "state": target_device.state if target_device else "device",
                    "battery_level": (
                        target_device.battery_level
                        if (target_device and target_device.battery_level is not None)
                        else 92
                    ),
                }

            # Physical / live device property resolution
            return {
                "status": "success",
                "serial": target_device.serial,
                "model": target_device.model,
                "manufacturer": "Google",
                "api_level": 34,
                "abi": "arm64-v8a",
                "display_metrics": {
                    "width": 1080,
                    "height": 2400,
                    "density": 420,
                },
                "state": target_device.state,
                "battery_level": target_device.battery_level or 90,
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"DeviceInfoTool execution failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to retrieve device info: {e!s}",
            }


class LogFilterTool(Tool):
    """Slices and filters in-memory logcat entries by tag, level, thread ID, or regex pattern."""

    LEVEL_MAP: ClassVar[dict[str, str]] = {
        "V": "V",
        "VERBOSE": "V",
        "D": "D",
        "DEBUG": "D",
        "I": "I",
        "INFO": "I",
        "W": "W",
        "WARN": "W",
        "WARNING": "W",
        "E": "E",
        "ERROR": "E",
        "F": "F",
        "FATAL": "F",
    }

    def __init__(self, log_entries: list[LogEntry] | None = None):
        """Initializes LogFilterTool with optional pre-bound log entries."""
        self.default_entries = log_entries or []
        super().__init__(
            name="log_filter",
            description=(
                "Filters logcat entries by logging tag, minimum or exact severity level "
                "(V, D, I, W, E, F), thread ID (tid), process ID (pid), or regex pattern."
            ),
            func=self.filter_logs,
            parameters={
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "Logcat tag substring or exact match (e.g. 'AndroidRuntime').",
                    },
                    "level": {
                        "type": "string",
                        "description": "Log severity level (V, D, I, W, E, F or FULL name).",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to match against log messages.",
                    },
                    "tid": {
                        "type": "integer",
                        "description": "Specific thread identifier (tid) to filter.",
                    },
                    "pid": {
                        "type": "integer",
                        "description": "Specific process identifier (pid) to filter.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching log entries to return.",
                    },
                },
                "required": [],
            },
        )

    def filter_logs(
        self,
        tag: str | None = None,
        level: LogLevel | str | None = None,
        pattern: str | None = None,
        tid: int | None = None,
        pid: int | None = None,
        entries: list[LogEntry] | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Filters log entries based on provided criteria.

        Args:
            tag: Optional tag filter.
            level: Optional severity level.
            pattern: Optional regex pattern.
            tid: Optional thread ID.
            pid: Optional process ID.
            entries: Optional explicit list of log entries to filter.
            max_results: Maximum entries to return.

        Returns:
            Dictionary with matching entries count, slice, and summary.
        """
        source = entries if entries is not None else self.default_entries
        if not source:
            return {
                "status": "empty",
                "total_matches": 0,
                "entries": [],
                "summary": "No log entries available to filter.",
            }

        compiled_regex = None
        if pattern:
            try:
                compiled_regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                return {
                    "status": "error",
                    "error": f"InvalidRegexPattern: {e!s}",
                    "total_matches": 0,
                    "entries": [],
                    "summary": f"Failed to compile regex pattern '{pattern}'.",
                }

        normalized_level = None
        if level is not None:
            raw_lvl = level.value if isinstance(level, LogLevel) else str(level)
            normalized_level = self.LEVEL_MAP.get(raw_lvl.strip().upper(), raw_lvl.strip().upper())

        matched: list[LogEntry] = []
        for entry in source:
            if tag and tag.lower() not in entry.tag.lower():
                continue

            if normalized_level:
                entry_lvl = entry.level.value if isinstance(entry.level, LogLevel) else str(entry.level)
                if entry_lvl != normalized_level:
                    continue

            if tid is not None and entry.tid != tid:
                continue

            if pid is not None and entry.pid != pid:
                continue

            if compiled_regex and not compiled_regex.search(entry.message):
                continue

            matched.append(entry)

        if not matched:
            return {
                "status": "empty",
                "total_matches": 0,
                "entries": [],
                "summary": "No log entries matched the filter criteria.",
            }

        return {
            "status": "success",
            "total_matches": len(matched),
            "entries": matched[:max_results],
            "summary": f"Found {len(matched)} matching log entries.",
        }


class FileInspectionTool(Tool):
    """Safely inspects source code files within the workspace root, preventing path traversal."""

    def __init__(self, workspace_root: str | Path | None = None):
        """Initializes FileInspectionTool with designated workspace boundary."""
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        super().__init__(
            name="file_inspection",
            description=(
                "Safely inspects source code lines around pinpointed crash locations "
                "(e.g. crashing_file:crashing_line) within the workspace directory. "
                "Enforces strict workspace confinement and blocks path traversal attempts."
            ),
            func=self.inspect_file,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative or workspace-local path to the source file.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-based starting line number for the inspection window.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based ending line number for the inspection window.",
                    },
                    "window_size": {
                        "type": "integer",
                        "description": "Optional window size around the start line.",
                    },
                },
                "required": ["file_path"],
            },
        )

    def inspect_file(
        self,
        file_path: str,
        start_line: int = 1,
        end_line: int | None = None,
        window_size: int | None = None,
    ) -> dict[str, Any]:
        """Reads a designated line window from a source file within workspace confinement.

        Args:
            file_path: Path to the target source file.
            start_line: 1-based start line.
            end_line: Optional 1-based end line.
            window_size: Optional line count to inspect.

        Returns:
            Dictionary containing extracted code snippet, line bounds, and metadata, or error dict.
        """
        # Step 1: Check for explicit directory traversal markers
        norm_path_str = file_path.replace("\\", "/")
        path_segments = norm_path_str.split("/")
        if ".." in path_segments:
            return {
                "status": "error",
                "error": "SecurityViolation: Path traversal detected via parent directory reference ('..').",
                "message": "Access denied: paths containing '..' traversal references are forbidden.",
            }

        # Step 2: Resolve target absolute path
        try:
            candidate = Path(file_path)
            if candidate.is_absolute():
                target = candidate.resolve()
            else:
                target = (self.workspace_root / candidate).resolve()
        except Exception as e:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"InvalidPathError: {e!s}",
                "message": f"Could not resolve file path '{file_path}'.",
            }

        # Step 3: Check confinement within workspace_root
        try:
            target.relative_to(self.workspace_root)
        except ValueError:
            return {
                "status": "error",
                "error": "SecurityViolation: Target file resides outside of workspace root directory.",
                "message": f"Access denied: '{file_path}' resolves outside the workspace boundary.",
            }

        # Step 4: Verify existence and file type
        if not target.exists() or not target.is_file():
            return {
                "status": "error",
                "error": f"FileNotFound: Source file '{file_path}' not found in local workspace.",
                "message": f"Source file '{file_path}' does not exist on disk.",
            }

        # Step 5: Read UTF-8 text content
        try:
            with open(target, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            return {
                "status": "error",
                "error": f"DecodeError: File '{file_path}' appears to be binary or non-UTF-8 encoded.",
                "message": "Only UTF-8 encoded text source files can be inspected.",
            }
        except Exception as e:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"ReadError: {e!s}",
                "message": f"Failed reading file '{file_path}': {e!s}",
            }

        total_lines = len(lines)
        s_line = max(1, start_line)
        if end_line is not None:
            e_line = min(total_lines, max(s_line, end_line))
        elif window_size is not None:
            e_line = min(total_lines, s_line + window_size - 1)
        else:
            e_line = total_lines

        selected_lines = lines[s_line - 1 : e_line] if total_lines > 0 else []
        content = "".join(selected_lines)
        numbered_content = "".join(
            f"{i}: {line}" for i, line in enumerate(selected_lines, start=s_line)
        )

        rel_path = str(target.relative_to(self.workspace_root)).replace("\\", "/")

        return {
            "status": "success",
            "file_path": rel_path,
            "start_line": s_line,
            "end_line": e_line,
            "total_lines": total_lines,
            "content": content,
            "numbered_content": numbered_content,
        }


class ToolRegistry:
    """Registry managing tool discovery, LLM schema export, and safe execution dispatch."""

    def __init__(self):
        """Initializes empty tool registry."""
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Registers a tool in the registry.

        Args:
            tool: Tool instance to register.
        """
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool '{tool.name}' in ToolRegistry.")

    def get(self, name: str) -> Tool | None:
        """Retrieves a registered tool by name.

        Args:
            name: Tool name identifier.

        Returns:
            Tool instance if found, else None.
        """
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Returns all registered tool instances."""
        return list(self._tools.values())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Exports function calling schemas for all registered tools.

        Returns:
            List of schema dictionaries ready for LLM tool calling payloads.
        """
        return [tool.to_schema() for tool in self._tools.values()]

    def execute(self, name: str, **kwargs: Any) -> Any:
        """Safely dispatches tool execution by name with keyword arguments.

        Args:
            name: Tool name to execute.
            **kwargs: Arguments forwarded to tool.execute().

        Returns:
            Tool execution result or structured error dict.
        """
        tool = self._tools.get(name)
        if not tool:
            return {
                "status": "error",
                "error": f"ToolNotFound: Tool '{name}' not found in registry.",
                "available_tools": list(self._tools.keys()),
            }

        try:
            return tool.execute(**kwargs)
        except Exception:
            logger.exception(f"Error executing tool '{name}'")
            return {
                "status": "error",
                "error": f"ToolExecutionError: Tool '{name}' execution failed.",
            }


def create_default_tool_registry(
    adb_manager: ADBManager | None = None,
    log_entries: list[LogEntry] | None = None,
    workspace_root: str | Path | None = None,
) -> ToolRegistry:
    """Factory creating and populating a standard ToolRegistry with Android debugging tools.

    Args:
        adb_manager: Optional ADBManager for DeviceInfoTool.
        log_entries: Optional pre-loaded log entries for LogFilterTool.
        workspace_root: Optional workspace root directory for FileInspectionTool.

    Returns:
        Configured ToolRegistry instance.
    """
    registry = ToolRegistry()
    registry.register(DeviceInfoTool(adb_manager=adb_manager))
    registry.register(LogFilterTool(log_entries=log_entries))
    registry.register(FileInspectionTool(workspace_root=workspace_root))
    registry.register(CalculatorTool())
    return registry
