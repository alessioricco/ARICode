"""Custom tool: verify the project actually works and report structured results.

Runs pytest for a Python project, or `npm run build` for a Node project with a
build script — whichever the workspace looks like — instead of always
assuming pytest. Added after a live failure where a scaffolded Vite/React
project had a JSX syntax error (`App.tsx` had adjacent, unwrapped JSX
elements); the agent declared the site "set up successfully" and pytest-only
verification silently reported "no tests collected" (exit code 5, treated as
"nothing to verify") for the exact same project — a false pass on code that
didn't even compile. `npm run build` runs the real bundler/transpiler
pipeline, which is what actually catches a parse/compile error like that one.
Detecting a project's actual JS test runner (jest/vitest/etc.) is still out
of scope — see ROADMAP.md "run_tests generalization".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence

from pydantic import Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor, register_tool

_FAILURE_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (.+)$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped)")

_PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
# Directories that are large, irrelevant to project-type detection, and
# expensive to walk into.
_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
# A scaffolded project can land a level or two below the workspace root
# passed via --project — confirmed live: `npm create vite@latest ...` nested
# the actual app under <project>/<name>/<name>/. Bounded so a large,
# unrelated workspace doesn't turn every verification call into a full tree
# walk.
_MAX_SCAN_DEPTH = 4


def _find_marker_dir(working_dir: str, names: set[str]) -> str | None:
    """First directory (breadth-first-ish via os.walk, depth-bounded) under
    `working_dir` containing any of `names`, or None."""
    base_depth = working_dir.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(working_dir):
        depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= _MAX_SCAN_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        if any(name in files for name in names):
            return root
    return None


def _detect_verification(working_dir: str) -> tuple[str, str]:
    """Return (kind, run_dir): 'pytest' (Python markers present, or none of
    either marker found — preserves this tool's original always-try-pytest
    default for a bare directory of test files), or 'npm_build' when a
    `package.json` with a `build` script is found and no Python markers are
    present. A project with both is treated as pytest — an unscoped
    monorepo edge case, not a real decision.
    """
    if _find_marker_dir(working_dir, set(_PYTHON_MARKERS)) is not None:
        return "pytest", working_dir
    package_json_dir = _find_marker_dir(working_dir, {"package.json"})
    if package_json_dir is not None:
        try:
            with open(os.path.join(package_json_dir, "package.json"), encoding="utf-8") as f:
                scripts = json.load(f).get("scripts", {})
        except (OSError, ValueError):
            scripts = {}
        if "build" in scripts:
            return "npm_build", package_json_dir
    return "pytest", working_dir


def _last_nonblank_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def _parse_counts(summary_line: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for num, kind in _COUNT_RE.findall(summary_line):
        key = "errors" if kind.startswith("error") else kind
        counts[key] += int(num)
    return counts


def _python_command() -> list[str]:
    """The interpreter to run pytest with.

    sys.executable is correct for a normal Python process (the same venv this
    tool runs under). Inside a PyInstaller-frozen process — e.g. this
    project's Docker agent-server image — it instead resolves to the frozen
    binary itself, not an interpreter, so fall back to whatever `python3` /
    `python` is on PATH there.
    """
    if getattr(sys, "frozen", False):
        return [shutil.which("python3") or shutil.which("python") or "python3"]
    return [sys.executable]


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
    check_kind: str = "pytest"  # "pytest" | "npm_build" | "none" (couldn't verify)
    exit_code: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failures: list[str] = Field(default_factory=list)
    summary: str = ""
    output: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        if self.check_kind == "npm_build":
            lines = [self.summary or f"npm run build exited with code {self.exit_code}"]
            if self.exit_code != 0 and self.output:
                lines.append(self.output)
            return [TextContent(text="\n".join(lines))]
        lines = [self.summary or f"pytest exited with code {self.exit_code}"]
        if self.failures:
            lines.append("Failures:")
            lines.extend(f"- {failure}" for failure in self.failures)
        return [TextContent(text="\n".join(lines))]


class RunTestsExecutor(ToolExecutor[RunTestsAction, RunTestsObservation]):
    def __init__(self, working_dir: str):
        self._working_dir = working_dir

    def __call__(self, action: RunTestsAction, conversation=None) -> RunTestsObservation:
        kind, run_dir = _detect_verification(self._working_dir)
        if kind == "npm_build":
            return self._run_npm_build(run_dir)
        return self._run_pytest(action, run_dir)

    def _run_pytest(self, action: RunTestsAction, run_dir: str) -> RunTestsObservation:
        cmd = [*_python_command(), "-m", "pytest", "-q"]
        if action.path:
            cmd.append(action.path)
        result = subprocess.run(
            cmd,
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        summary = _last_nonblank_line(output)
        counts = _parse_counts(summary)
        failures = _FAILURE_LINE_RE.findall(output)
        return RunTestsObservation(
            check_kind="pytest",
            exit_code=result.returncode,
            passed=counts["passed"],
            failed=counts["failed"],
            errors=counts["errors"],
            failures=failures,
            summary=summary,
            output=output[-4000:],
        )

    def _run_npm_build(self, run_dir: str) -> RunTestsObservation:
        npm = shutil.which("npm")
        if npm is None:
            return RunTestsObservation(
                check_kind="none",
                summary="npm not found on PATH; cannot verify the build",
            )
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        summary = (
            "npm run build succeeded"
            if result.returncode == 0
            else f"npm run build failed (exit {result.returncode})"
        )
        return RunTestsObservation(
            check_kind="npm_build",
            exit_code=result.returncode,
            summary=summary,
            output=output[-4000:],
        )


class RunTestsTool(ToolDefinition[RunTestsAction, RunTestsObservation]):
    """Verify the project actually works. For a Python project: run its pytest
    suite (optionally a single file or node id) and report structured
    pass/fail/error counts, a one-line summary, and the list of failing test
    node ids. For a Node project with a `build` script (e.g. Vite/React):
    run `npm run build` instead, which catches compile/parse errors (a
    syntax error, an unclosed JSX tag, a type error) — report its exit
    status and raw output."""

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


# Registering at import time (not inside build_run_tests_tool) means importing
# this module is enough to make the tool available — which is what lets a
# containerized agent-server register it via `--import-modules
# harness.custom_tools` (see docker/agent-server.Dockerfile), not just the
# local process that calls build_run_tests_tool() directly.
register_tool(RunTestsTool.name, RunTestsTool)


def build_run_tests_tool() -> Tool:
    return Tool(name=RunTestsTool.name)
