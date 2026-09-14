"""Custom tool: run the project's pytest suite and report structured results.

Lets the agent run tests and read pass/fail counts plus failing node ids
directly, instead of shelling out through the generic terminal tool and
hand-parsing raw pytest output itself.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence

from pydantic import Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor, register_tool

_FAILURE_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (.+)$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped)")


def _last_nonblank_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def _parse_counts(summary_line: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for num, kind in _COUNT_RE.findall(summary_line):
        key = "errors" if kind.startswith("error") else kind
        counts[key] += int(num)
    return counts


class RunTestsAction(Action):
    path: str = Field(
        default="",
        description=(
            "Optional test path or node id to run, relative to the workspace "
            "(e.g. 'tests/test_config.py' or 'tests/test_config.py::test_foo'). "
            "Empty runs the full suite."
        ),
    )


class RunTestsObservation(Observation):
    exit_code: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failures: list[str] = Field(default_factory=list)
    summary: str = ""
    output: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        lines = [self.summary or f"pytest exited with code {self.exit_code}"]
        if self.failures:
            lines.append("Failures:")
            lines.extend(f"- {failure}" for failure in self.failures)
        return [TextContent(text="\n".join(lines))]


class RunTestsExecutor(ToolExecutor[RunTestsAction, RunTestsObservation]):
    def __init__(self, working_dir: str):
        self._working_dir = working_dir

    def __call__(self, action: RunTestsAction, conversation=None) -> RunTestsObservation:
        cmd = [sys.executable, "-m", "pytest", "-q"]
        if action.path:
            cmd.append(action.path)
        result = subprocess.run(
            cmd,
            cwd=self._working_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        summary = _last_nonblank_line(output)
        counts = _parse_counts(summary)
        failures = _FAILURE_LINE_RE.findall(output)
        return RunTestsObservation(
            exit_code=result.returncode,
            passed=counts["passed"],
            failed=counts["failed"],
            errors=counts["errors"],
            failures=failures,
            summary=summary,
            output=output[-4000:],
        )


class RunTestsTool(ToolDefinition[RunTestsAction, RunTestsObservation]):
    """Run the project's pytest suite (optionally a single file or node id) and
    report structured pass/fail/error counts, a one-line summary, and the list
    of failing test node ids."""

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[ToolDefinition]:
        working_dir = str(conv_state.workspace.working_dir)
        return [
            cls(
                description=cls.__doc__ or "",
                action_type=RunTestsAction,
                observation_type=RunTestsObservation,
                executor=RunTestsExecutor(working_dir),
            )
        ]


def build_run_tests_tool() -> Tool:
    register_tool(RunTestsTool.name, RunTestsTool)
    return Tool(name=RunTestsTool.name)
