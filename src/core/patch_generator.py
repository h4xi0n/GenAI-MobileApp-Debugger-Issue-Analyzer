"""IDE Patch and Action Prompt Generator Bridge.

Generates unified diffs and formats remediation prompts tailored for Cursor Composer
and Antigravity MAS specialist subagents.

Author: Arun Krishnan
"""

import difflib

from .logger import setup_logger
from .models import DiagnosticReport, IDEActionPrompt

logger = setup_logger("patch_generator")


class PatchGenerator:
    """Synthesizes unified git diffs and formats developer prompts for AI-assisted IDEs."""

    @staticmethod
    def generate_unified_diff(
        original_content: str,
        modified_content: str,
        fromfile: str = "a/target.kt",
        tofile: str = "b/target.kt",
    ) -> str:
        """Generates standard unified diff output using difflib.

        Args:
            original_content: Raw source code string prior to modification.
            modified_content: Corrected source code string.
            fromfile: Path label for original file.
            tofile: Path label for modified file.

        Returns:
            Unified diff formatted string with standard header.
        """
        orig_lines = original_content.splitlines(keepends=True)
        mod_lines = modified_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            orig_lines,
            mod_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm=""
        )
        return "".join(diff)

    @classmethod
    def format_cursor_prompt(
        cls,
        report: DiagnosticReport,
        target_file: str | None = None,
        original_code: str | None = None,
        modified_code: str | None = None,
    ) -> IDEActionPrompt:
        """Constructs a ready-to-paste markdown action prompt for Cursor Composer.

        Args:
            report: Diagnostic report detailing detected exception and suggested remediation.
            target_file: Optional explicit target source file to modify.
            original_code: Optional original code string for diff synthesis.
            modified_code: Optional modified code string for diff synthesis.

        Returns:
            IDEActionPrompt formatted with @file tags and markdown instructions.
        """
        resolved_file = target_file
        if not resolved_file and report.affected_components:
            resolved_file = report.affected_components[0]
        if not resolved_file and report.crash_signature and report.crash_signature.crashing_file:
            resolved_file = f"app/src/main/java/{report.crash_signature.crashing_file}"
        if not resolved_file:
            resolved_file = "app/src/main/java/com/example/MainActivity.kt"

        diff_str = None
        if original_code is not None and modified_code is not None:
            diff_str = cls.generate_unified_diff(
                original_code,
                modified_code,
                fromfile=f"a/{resolved_file}",
                tofile=f"b/{resolved_file}"
            )

        instructions = (
            f"1. Open @{resolved_file}\n"
            f"2. {report.suggested_fix_summary}\n"
            "3. Ensure safe null-checking and lifecycle-aware state observation.\n"
            "4. Verify project compiles with `./gradlew assembleDebug`."
        )

        prompt_lines = [
            "### 🤖 Cursor Composer Action Prompt",
            f"**Target:** `@{resolved_file}`",
            "",
            f"**Diagnosis:** {report.error_summary}",
            f"**Root Cause:** {report.root_cause_analysis}",
            f"**Confidence Score:** {int(report.confidence_score * 100)}%",
            "",
            "**Remediation Steps:**",
            instructions,
        ]

        if diff_str:
            prompt_lines.extend([
                "",
                "**Proposed Diff:**",
                "```diff",
                diff_str,
                "```"
            ])

        formatted_text = "\n".join(prompt_lines)

        return IDEActionPrompt(
            target_ide="cursor",
            file_targets=[resolved_file],
            instructions=instructions,
            proposed_diff=diff_str,
            formatted_prompt=formatted_text,
        )

    @classmethod
    def format_antigravity_handoff(
        cls,
        report: DiagnosticReport,
        target_file: str | None = None,
        original_code: str | None = None,
        modified_code: str | None = None,
        task_id: str | None = None,
    ) -> IDEActionPrompt:
        """Constructs a MAS specialist subagent handoff for Antigravity.

        Args:
            report: Diagnostic report detailing detected exception and suggested remediation.
            target_file: Optional explicit target source file to modify.
            original_code: Optional original code string for diff synthesis.
            modified_code: Optional modified code string for diff synthesis.
            task_id: Optional task identifier (e.g. 'FIX-01').

        Returns:
            IDEActionPrompt formatted as a structured specialist handoff document.
        """
        resolved_file = target_file
        if not resolved_file and report.affected_components:
            resolved_file = report.affected_components[0]
        if not resolved_file and report.crash_signature and report.crash_signature.crashing_file:
            resolved_file = f"app/src/main/java/{report.crash_signature.crashing_file}"
        if not resolved_file:
            resolved_file = "app/src/main/java/com/example/MainActivity.kt"

        resolved_task_id = task_id or f"FIX-{report.report_id.split('-')[-1]}"

        diff_str = None
        if original_code is not None and modified_code is not None:
            diff_str = cls.generate_unified_diff(
                original_code,
                modified_code,
                fromfile=f"a/{resolved_file}",
                tofile=f"b/{resolved_file}"
            )

        instructions = (
            f"1. Apply the suggested fix to `{resolved_file}`: {report.suggested_fix_summary}\n"
            "2. Run unit tests and static analysis (`./gradlew testDebugUnitTest`).\n"
            "3. Verify zero regression in surrounding Compose state holders."
        )

        handoff_lines = [
            "### 🪐 Antigravity Specialist Subagent Handoff",
            "ROLE: general-dev",
            f"TASK_ID: {resolved_task_id}",
            "FILES:",
            f"* {resolved_file}",
            f"OBJECTIVE: Remediate {report.error_summary} (Report #{report.report_id})",
            "INSTRUCTIONS:",
            instructions,
        ]

        if diff_str:
            handoff_lines.extend([
                "DIFF:",
                "```diff",
                diff_str,
                "```"
            ])

        handoff_lines.append("DONE")
        formatted_text = "\n".join(handoff_lines)

        return IDEActionPrompt(
            target_ide="antigravity",
            file_targets=[resolved_file],
            instructions=instructions,
            proposed_diff=diff_str,
            formatted_prompt=formatted_text,
        )
