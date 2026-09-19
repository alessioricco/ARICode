"""Custom tool: verify the project actually works and report structured results.

Language-neutral verification, in four explicit stages (see MANUAL.md "Test
verification"):

1. **Project detection** (`detect_project`) — what kind of project is this,
   and from which directory should commands run.
2. **Verification-plan discovery** (`discover_verification_plan`) — given a
   detected project, which commands apply, resolved against what the
   project itself configures — never invented. Pure/no subprocess calls:
   deciding *what* to run is kept separate from actually running it.
3. **Verification command execution** (`execute_check`) — runs one planned
   check and turns it into a structured `CheckOutcome`. The only stage that
   spawns a subprocess.
4. **Structured verification results** (`CheckOutcome` / `VerificationRun`) —
   the result shape both the harness's post-hoc loop (`runner.py`) and this
   module's own `run_full_verification()` orchestration consume.

Originally Python(pytest)/Node(`npm run build`)-only. Added after a live
failure where a scaffolded Vite/React project had a JSX syntax error
(`App.tsx` had adjacent, unwrapped JSX elements); the agent declared the
site "set up successfully" and pytest-only verification silently reported
"no tests collected" (exit code 5, treated as "nothing to verify") for the
exact same project — a false pass on code that didn't even compile.
Generalized to Go/Rust/Java(Maven+Gradle) plus Python/Node lint+typecheck
and Node lint/typecheck scripts per explicit follow-up request — see
ROADMAP.md's decisions log for what's genuinely verified vs. only detected.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor, register_tool

_FAILURE_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (.+)$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped)")

# pytest's own exit code for "ran cleanly but collected zero tests" — not a
# failure, just nothing to verify (a project with no tests yet). Every other
# nonzero exit code means tests actually ran and something is wrong. No
# leading underscore: runner.py needs this same constant — see MANUAL.md
# "Test verification".
PYTEST_NO_TESTS_COLLECTED = 5

# Directories that are large, irrelevant to project-type detection, and
# expensive to walk into.
_SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",  # Rust/Java (Cargo, Maven) build output
    "vendor",  # Go
}
# A scaffolded project can land a level or two below the workspace root
# passed via --project — confirmed live: `npm create vite@latest ...` nested
# the actual app under <project>/<name>/<name>/. Bounded so a large,
# unrelated workspace doesn't turn every verification call into a full tree
# walk.
_MAX_SCAN_DEPTH = 4

# Marker files that unambiguously identify a project's language/ecosystem,
# checked in this order (so, e.g., a directory with both `pyproject.toml`
# and `package.json` is treated as Python — an unscoped monorepo edge case,
# not a real per-project decision). Each of these is a *single* well-known
# manifest format per ecosystem, which is what "unambiguous" means here —
# deliberately not extended to every possible build tool.
_LANGUAGE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", ("pyproject.toml", "setup.py", "setup.cfg")),
    ("node", ("package.json",)),
    ("go", ("go.mod",)),
    ("rust", ("Cargo.toml",)),
    ("java-maven", ("pom.xml",)),
    ("java-gradle", ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")),
)


def _find_marker_dir(working_dir: str, names: set[str]) -> str | None:
    """First directory (depth-bounded `os.walk`) under `working_dir`
    containing any of `names`, or None."""
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


@dataclass(frozen=True)
class ProjectDetection:
    """Stage 1 result: what kind of project this is, and where its
    verification commands should run from.

    `candidates` is only set when `language == "ambiguous"`: every
    (language, root) pair found at the shallowest depth where more than one
    distinct directory matched, so a caller/message can name them instead of
    silently picking one (see `detect_project`'s docstring).
    """

    # "python" | "node" | "go" | "rust" | "java-maven" | "java-gradle" |
    # "ambiguous" | "unknown"
    language: str
    root: str
    candidates: tuple[tuple[str, str], ...] | None = None


def detect_project(working_dir: str) -> ProjectDetection:
    """Detect the project's language/ecosystem from unambiguous manifest
    markers — a single depth-bounded tree walk checking every known
    ecosystem's markers together (not one walk per language), collecting
    every match grouped by depth so the *truly* shallowest directory/
    directories can be compared, rather than whichever one a top-down
    `os.walk` happens to visit first (a DFS visits an entire first branch,
    however deep, before a shallower sibling branch — "first found" and
    "shallowest" are not the same thing).

    If `working_dir` itself matches (depth 0), that's authoritative and
    returned immediately — the caller already told us this exact directory
    is the project (e.g. `HARNESS_WORKSPACE`/`--project`), so there is
    nothing to disambiguate even if deeper subdirectories also happen to
    contain manifests (a project's own vendored dependency, a nested
    example, etc.).

    Otherwise, once the shallowest depth containing any match is found: if
    exactly one directory matched there, that's the project, same as
    before. If *multiple distinct directories* matched at that same
    shallowest depth — a monorepo/workspace with more than one candidate
    project and no single obvious root — this reports `"ambiguous"` with
    every candidate listed on `.candidates`, rather than silently choosing
    one based on directory-walk/listing order. (A single directory matching
    more than one language's markers, e.g. both `pyproject.toml` and
    `package.json` in the same directory, is a different, narrower case and
    is still resolved by `_LANGUAGE_MARKERS` order — not what "ambiguous"
    means here.)

    Falls back to "python" when no manifest is found but real Python source
    is present (`*.py` files) — this is what keeps a bare directory of
    `test_*.py` files (this repo's own test fixtures, and plenty of small
    real scripts with no `pyproject.toml`) working exactly as before. Only
    when *neither* a manifest *nor* any Python source is found does this
    report `"unknown"` — verification-plan discovery turns that into an
    explicit "verification unavailable" result rather than silently
    guessing a command (see `discover_verification_plan`).
    """
    base_depth = working_dir.rstrip(os.sep).count(os.sep)
    matches_by_depth: dict[int, list[tuple[str, str]]] = {}
    python_source_root: str | None = None
    for root, dirs, files in os.walk(working_dir):
        depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= _MAX_SCAN_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        if depth == 0:
            for language, markers in _LANGUAGE_MARKERS:
                if any(marker in files for marker in markers):
                    return ProjectDetection(language=language, root=root)
        else:
            for language, markers in _LANGUAGE_MARKERS:
                if any(marker in files for marker in markers):
                    matches_by_depth.setdefault(depth, []).append((language, root))
                    break  # one match per directory — same-dir ties are not "ambiguous"
        if python_source_root is None and any(f.endswith(".py") for f in files):
            python_source_root = root
    if matches_by_depth:
        shallowest = matches_by_depth[min(matches_by_depth)]
        if len(shallowest) == 1:
            language, root = shallowest[0]
            return ProjectDetection(language=language, root=root)
        return ProjectDetection(
            language="ambiguous", root=working_dir, candidates=tuple(shallowest)
        )
    if python_source_root is not None:
        return ProjectDetection(language="python", root=python_source_root)
    return ProjectDetection(language="unknown", root=working_dir)


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
            "Python projects only — empty runs the full suite."
        ),
    )


class RunTestsObservation(Observation):
    # "pytest" | "npm_build" | "go" | "rust" | "java-maven" | "java-gradle" |
    # "ambiguous" | "unknown" | "none" (couldn't verify at all, e.g. npm missing)
    check_kind: str = "pytest"
    exit_code: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failures: list[str] = Field(default_factory=list)
    summary: str = ""
    output: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        if self.check_kind == "pytest":
            lines = [self.summary or f"pytest exited with code {self.exit_code}"]
            if self.failures:
                lines.append("Failures:")
                lines.extend(f"- {failure}" for failure in self.failures)
            return [TextContent(text="\n".join(lines))]
        lines = [self.summary or f"verification exited with code {self.exit_code}"]
        if self.exit_code != 0 and self.output:
            lines.append(self.output)
        return [TextContent(text="\n".join(lines))]


class RunTestsExecutor(ToolExecutor[RunTestsAction, RunTestsObservation]):
    def __init__(self, working_dir: str):
        self._working_dir = working_dir

    def __call__(self, action: RunTestsAction, conversation=None) -> RunTestsObservation:
        detection = detect_project(self._working_dir)
        if detection.language == "python":
            return self._run_pytest(action, detection.root)
        if detection.language == "node":
            return self._run_npm_build(detection.root)
        # Go/Rust/Java/unknown: the agent-facing tool always runs exactly
        # one check (its contract, unchanged) — reuse the same plan/execute
        # pipeline the harness's own post-hoc verification uses (stages 2-3
        # below) instead of a second, separate detection path, and adapt its
        # richer `CheckOutcome` into this tool's legacy `Observation` shape.
        plan = discover_verification_plan(detection)
        primary = next((spec for spec in plan if spec.primary), plan[0])
        outcome = execute_check(primary)
        return RunTestsObservation(
            check_kind=detection.language,
            exit_code=outcome.exit_code or 0,
            summary=outcome.summary,
            output=outcome.output,
        )

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
    """Verify the project actually works, whatever language it's written in
    (Python, Node/JS/TS, Go, Rust, or Java). Runs the project's single
    primary check — its test suite (Python: pytest; Go: `go test ./...`;
    Rust: `cargo test`; Java: Maven/Gradle `test`) or, for a Node project
    with no test script but a `build` script (e.g. Vite/React), `npm run
    build` — catching compile/parse errors a test suite might never
    exercise. Reports the command's exit status and raw output; for pytest,
    also structured pass/fail/error counts and failing node ids."""

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


# --- Harness-side, multi-check verification (not exposed to the LLM) -------
#
# The agent-facing `run_tests` tool above always runs exactly one check —
# that contract stays stable and unit-tested as-is. Everything below is the
# language-neutral pipeline the harness itself drives post-hoc, outside any
# tool-call round trip: stage 2 (verification-plan discovery, pure — no
# subprocess calls) decides *what* to run per detected language, stage 3
# (command execution) actually runs it, and stage 4 (these result types)
# aggregates. See runner.py's verification loop for how this feeds the
# retry/completion-contract logic.

# The five outcomes a single check can end in. "skipped" is a check that
# was never even attempted because it doesn't apply here (no lint config,
# no test script defined) *or* ran but found nothing to verify (pytest
# collected zero tests) — a legitimate, non-alarming absence. "unavailable"
# is a check that *should* have been attempted but couldn't be — its tool
# isn't installed, or (for the project's one synthetic "verification" check
# on an unknown project) no known command could be inferred at all. Never
# conflate the two: "skipped" isn't a limitation worth reporting; every
# "unavailable" is.
CHECK_STATUSES = ("passed", "failed", "skipped", "unavailable", "timed_out")


@dataclass(frozen=True)
class CheckOutcome:
    """One executed (or deliberately skipped/unavailable) verification check."""

    name: str  # e.g. "pytest", "ruff", "go test", "cargo clippy"
    command: str  # the actual command line, for the completion contract
    primary: bool  # the project's main test/build check vs. a secondary one
    status: str  # one of CHECK_STATUSES
    exit_code: int | None  # None when status is "skipped"/"unavailable"
    project_root: str
    summary: str
    output: str


@dataclass(frozen=True)
class VerificationRun:
    """The aggregate result of every check attempted for one verification pass."""

    checks: list[CheckOutcome]

    @property
    def state(self) -> str:
        """'failed' if anything actually ran and came back nonzero (highest
        priority — never let a timeout or a pass elsewhere hide a real
        failure); else 'timed_out' if anything exceeded its timeout; else
        'verified' only if the project's *primary* check ran and passed —
        a secondary check (lint/typecheck) can only ever pull this down to
        'failed', never promote it to 'verified' on its own, since tidiness
        isn't proof the software works; else 'inconclusive'."""
        if any(c.status == "failed" for c in self.checks):
            return "failed"
        if any(c.status == "timed_out" for c in self.checks):
            return "timed_out"
        primary = next((c for c in self.checks if c.primary), None)
        if primary is not None and primary.status == "passed":
            return "verified"
        return "inconclusive"

    @property
    def failing(self) -> list[CheckOutcome]:
        return [c for c in self.checks if c.status == "failed"]

    @property
    def timed_out_checks(self) -> list[CheckOutcome]:
        return [c for c in self.checks if c.status == "timed_out"]

    @property
    def commands(self) -> list[str]:
        return [c.command for c in self.checks if c.command]

    @property
    def limitation_notes(self) -> list[str]:
        """One human-readable note per check the harness *should* have been
        able to verify but couldn't — e.g. "ruff is configured but not
        installed," or "no known project type was detected." Deliberately
        excludes "skipped" checks (not configured, or nothing to test) —
        those aren't gaps, they're a correct absence."""
        return [f"{c.name}: {c.summary}" for c in self.checks if c.status == "unavailable"]


@dataclass(frozen=True)
class CheckSpec:
    """Stage 2 result: one planned verification check, not yet executed.

    `command=None` means nothing will run — either `skip_reason` (not
    applicable/configured) or `unavailable_reason` (should be checkable but
    isn't, e.g. a missing tool) explains why, and `execute_check` turns
    that directly into a "skipped"/"unavailable" `CheckOutcome` with no
    subprocess call at all.
    """

    name: str
    primary: bool
    command: list[str] | None
    cwd: str
    env: dict[str, str] | None = None
    # Exit codes that mean "ran, found nothing to verify" rather than pass
    # or fail — pytest's PYTEST_NO_TESTS_COLLECTED is the only current use.
    skip_exit_codes: tuple[int, ...] = ()
    skip_reason: str | None = None
    unavailable_reason: str | None = None
    # A check whose result is already known at planning time — a pure
    # static check (e.g. an AST-based structural check) needs no subprocess
    # at all. `execute_check` returns this directly when set; `command`
    # must be None alongside it.
    precomputed: CheckOutcome | None = None


_CHECK_TIMEOUT_SECONDS = 300


def execute_check(spec: CheckSpec, *, timeout: int = _CHECK_TIMEOUT_SECONDS) -> CheckOutcome:
    """Stage 3: run one planned check. The only function in this module that
    spawns a subprocess for harness-side verification — except a
    `precomputed` spec, which by definition needed none."""
    if spec.precomputed is not None:
        return spec.precomputed
    if spec.command is None:
        status = "unavailable" if spec.unavailable_reason else "skipped"
        summary = spec.unavailable_reason or spec.skip_reason or f"{spec.name} does not apply here"
        return CheckOutcome(
            name=spec.name,
            command="",
            primary=spec.primary,
            status=status,
            exit_code=None,
            project_root=spec.cwd,
            summary=summary,
            output="",
        )

    command_str = " ".join(spec.command)
    try:
        result = subprocess.run(
            spec.command,
            cwd=spec.cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=spec.env,
            # No human is available to answer a prompt (same rationale as
            # agent.py's _NONINTERACTIVE_TOOLING_SUFFIX) — without this, a
            # subprocess that reads stdin would inherit the harness's own,
            # which can hang this check indefinitely rather than failing
            # fast. Also what lets the entry-point smoke-test check below
            # observe how a script behaves with no input available, instead
            # of blocking on it.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        return CheckOutcome(
            name=spec.name,
            command=command_str,
            primary=spec.primary,
            status="timed_out",
            exit_code=None,
            project_root=spec.cwd,
            summary=f"{spec.name} exceeded its {timeout}s timeout and was killed",
            output=partial[-4000:],
        )
    except OSError as exc:
        return CheckOutcome(
            name=spec.name,
            command=command_str,
            primary=spec.primary,
            status="unavailable",
            exit_code=None,
            project_root=spec.cwd,
            summary=f"{spec.name} could not be run: {exc}",
            output="",
        )

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode in spec.skip_exit_codes:
        return CheckOutcome(
            name=spec.name,
            command=command_str,
            primary=spec.primary,
            status="skipped",
            exit_code=result.returncode,
            project_root=spec.cwd,
            summary=spec.skip_reason or f"{spec.name}: nothing to verify",
            output="",
        )
    passed = result.returncode == 0
    summary = f"{spec.name} {'passed' if passed else f'failed (exit {result.returncode})'}"
    return CheckOutcome(
        name=spec.name,
        command=command_str,
        primary=spec.primary,
        status="passed" if passed else "failed",
        exit_code=result.returncode,
        project_root=spec.cwd,
        summary=summary,
        output=output[-4000:],
    )


def _pyproject_has_tool_section(run_dir: str, section: str) -> bool:
    path = os.path.join(run_dir, "pyproject.toml")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return section in data.get("tool", {})


def _ruff_configured(run_dir: str) -> bool:
    return (
        _pyproject_has_tool_section(run_dir, "ruff")
        or os.path.isfile(os.path.join(run_dir, "ruff.toml"))
        or os.path.isfile(os.path.join(run_dir, ".ruff.toml"))
    )


def _mypy_configured(run_dir: str) -> bool:
    return (
        _pyproject_has_tool_section(run_dir, "mypy")
        or os.path.isfile(os.path.join(run_dir, "mypy.ini"))
        or os.path.isfile(os.path.join(run_dir, ".mypy.ini"))
    )


def _node_scripts(run_dir: str) -> dict[str, str]:
    path = os.path.join(run_dir, "package.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("scripts", {})
    except (OSError, ValueError):
        return {}


def _real_node_script(scripts: dict[str, str], key: str) -> str | None:
    """A `package.json` script worth running, or None — filters out
    `npm init`'s default placeholder test script
    (`"echo \\"Error: no test specified\\" && exit 1"`), which always
    "fails" and would report a false regression for a project that simply
    never defined tests."""
    script = scripts.get(key)
    if not script or "no test specified" in script.lower():
        return None
    return script


def _npm_test_script(run_dir: str) -> str | None:
    return _real_node_script(_node_scripts(run_dir), "test")


# --- Python entry-point checks ----------------------------------------------
#
# A passing pytest suite only proves the code behaves correctly when
# *imported* — confirmed live this is a real, distinct gap: a script defined
# a function *after* its own `if __name__ == "__main__":` guard, and the
# function it called only when a particular interactive command was chosen.
# Importing the module (exactly what a test suite does) runs the whole file
# top-to-bottom and never enters the guarded block, so the function exists
# by the time any test could call it; running the script directly calls into
# the guarded block immediately, before the interpreter ever reaches that
# later definition — `NameError`, invisible to every test, visible to every
# real user. Two checks, from two different angles: `_entrypoint_ordering_spec`
# is a static, structural check for exactly this ordering mistake; the
# `python <entry point>` `CheckSpec`s built in `_python_plan` actually run
# each entry point (stdin closed — see `execute_check`) to catch startup
# crashes generally, this ordering bug included if it happens to fire before
# any input is read (not guaranteed — this one only reaches the guarded
# `main()` call, not necessarily the specific branch inside it that broke
# live; the static check is what caught that precisely).


def _is_main_guard(node: ast.stmt) -> bool:
    """True for `if __name__ == "__main__":` (either operand order)."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not (
        isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    operands = (test.left, test.comparators[0])
    names = [o for o in operands if isinstance(o, ast.Name)]
    consts = [o for o in operands if isinstance(o, ast.Constant)]
    return (
        len(names) == 1
        and len(consts) == 1
        and names[0].id == "__name__"
        and consts[0].value == "__main__"
    )


def _find_python_entrypoints(root: str) -> list[str]:
    """`.py` filenames directly in `root` whose top level contains a
    `if __name__ == "__main__":` guard — deliberately not recursive (an
    "entry point" is expected at the project root for the kind of small
    generated project this harness verifies) and skips a file that fails to
    parse (a real syntax error surfaces via pytest collection instead)."""
    entrypoints = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for name in entries:
        if not name.endswith(".py"):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        if any(_is_main_guard(node) for node in tree.body):
            entrypoints.append(name)
    return entrypoints


def _entrypoint_ordering_spec(root: str, entrypoints: list[str]) -> CheckSpec | None:
    """A precomputed (no-subprocess) static check: for each detected entry
    point, is its `if __name__ == "__main__":` guard the file's *last*
    top-level statement? A `def`/`class` appearing after the guard won't
    exist yet when the script is run directly, even though it's fully
    defined by the time anything `import`s the module. Returns None only
    when there are no entry points to check at all — with at least one, this
    always contributes a check (`passed` or `failed`), the same way pytest
    always runs for a Python project; it isn't gated on project config,
    since it isn't optional/configurable, it's a language-level trap.
    """
    if not entrypoints:
        return None
    problems: list[str] = []
    for name in entrypoints:
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        guard_index = next((i for i, node in enumerate(tree.body) if _is_main_guard(node)), None)
        if guard_index is None:
            continue
        later_defs = [
            node.name
            for node in tree.body[guard_index + 1 :]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        if later_defs:
            problems.append(
                f'{name}: `if __name__ == "__main__":` is not the file\'s last '
                f"top-level statement — {', '.join(later_defs)} defined after it "
                "will not exist yet when this script is run directly, even though "
                "importing the module (what pytest does) sees it fine. Move the "
                "__main__ guard (and whatever it calls) to the end of the file, "
                "after every definition it needs."
            )
    outcome = CheckOutcome(
        name="entrypoint-ordering",
        command="",
        primary=False,
        status="failed" if problems else "passed",
        exit_code=None,
        project_root=root,
        summary=(
            "entry point defines something after its own __main__ guard"
            if problems
            else "entry point definition order OK"
        ),
        output="\n".join(problems),
    )
    return CheckSpec(
        name="entrypoint-ordering", primary=False, command=None, cwd=root, precomputed=outcome
    )


def _python_plan(root: str) -> list[CheckSpec]:
    specs = [
        CheckSpec(
            name="pytest",
            primary=True,
            command=[*_python_command(), "-m", "pytest", "-q"],
            cwd=root,
            skip_exit_codes=(PYTEST_NO_TESTS_COLLECTED,),
            skip_reason="no tests collected",
        )
    ]
    if _ruff_configured(root):
        ruff = shutil.which("ruff")
        if ruff is not None:
            specs.append(
                CheckSpec(name="ruff", primary=False, command=[ruff, "check", "."], cwd=root)
            )
        else:
            specs.append(
                CheckSpec(
                    name="ruff",
                    primary=False,
                    command=None,
                    cwd=root,
                    unavailable_reason="ruff is configured but not installed; lint was not verified",
                )
            )
    if _mypy_configured(root):
        mypy = shutil.which("mypy")
        if mypy is not None:
            specs.append(CheckSpec(name="mypy", primary=False, command=[mypy, "."], cwd=root))
        else:
            specs.append(
                CheckSpec(
                    name="mypy",
                    primary=False,
                    command=None,
                    cwd=root,
                    unavailable_reason=(
                        "mypy is configured but not installed; type checking was not verified"
                    ),
                )
            )

    entrypoints = _find_python_entrypoints(root)
    ordering_spec = _entrypoint_ordering_spec(root, entrypoints)
    if ordering_spec is not None:
        specs.append(ordering_spec)
    # Actually run each entry point (not just import it, which pytest already
    # does) — stdin is closed (see execute_check), so this only proves the
    # script doesn't crash before/without reading input; it cannot exercise
    # an interactive menu path like a "SOLVE" command. That's a real,
    # deliberate scope limit, not an oversight — see MANUAL.md "Test
    # verification".
    for name in entrypoints:
        specs.append(
            CheckSpec(
                name=f"python {name}",
                primary=False,
                command=[*_python_command(), name],
                cwd=root,
            )
        )
    return specs


# npm scripts checked, in priority order for picking the *primary* check:
# a `build` script exists → build is primary (it's what actually catches a
# compile/parse error, per this tool's own history — see module docstring);
# else a real `test` script → test is primary. `lint`/`typecheck` are always
# secondary when present. Nothing here is invented: a script only becomes a
# check if the project's own package.json defines it.
def _node_plan(root: str) -> list[CheckSpec]:
    npm = shutil.which("npm")
    scripts = _node_scripts(root)
    build = _real_node_script(scripts, "build")
    test = _real_node_script(scripts, "test")
    lint = _real_node_script(scripts, "lint")
    typecheck_key = "typecheck" if _real_node_script(scripts, "typecheck") else "type-check"
    typecheck = _real_node_script(scripts, typecheck_key)

    primary_key = "build" if build else ("test" if test else None)
    if primary_key is None:
        return [
            CheckSpec(
                name="npm run <build|test>",
                primary=True,
                command=None,
                cwd=root,
                unavailable_reason="package.json defines no build or test script",
            )
        ]

    def spec_for(key: str, *, primary: bool, use_ci_env: bool) -> CheckSpec:
        name = f"npm run {key}" if key != "test" else "npm test"
        if npm is None:
            return CheckSpec(
                name=name,
                primary=primary,
                command=None,
                cwd=root,
                unavailable_reason="npm not found on PATH",
            )
        command = [npm, "test"] if key == "test" else [npm, "run", key]
        # CI=true is the widely-adopted convention (CRA's react-scripts,
        # Vitest, Jest) for making a test/lint runner exit after one pass
        # instead of defaulting to an interactive watch mode with no human
        # to drive it — same rationale as agent.py's
        # _NONINTERACTIVE_TOOLING_SUFFIX, applied to the harness's own
        # subprocess calls. `build` isn't a watch-mode command, so it's
        # left out of this.
        env = {**os.environ, "CI": "true"} if use_ci_env else None
        return CheckSpec(name=name, primary=primary, command=command, cwd=root, env=env)

    specs = [spec_for(primary_key, primary=True, use_ci_env=primary_key != "build")]
    if primary_key != "test" and test:
        specs.append(spec_for("test", primary=False, use_ci_env=True))
    if lint:
        specs.append(spec_for("lint", primary=False, use_ci_env=True))
    if typecheck:
        specs.append(spec_for(typecheck_key, primary=False, use_ci_env=True))
    return specs


def _go_plan(root: str) -> list[CheckSpec]:
    go = shutil.which("go")
    if go is None:
        return [
            CheckSpec(
                name="go test",
                primary=True,
                command=None,
                cwd=root,
                unavailable_reason="go is configured (go.mod present) but not installed",
            )
        ]
    return [
        CheckSpec(name="go test", primary=True, command=[go, "test", "./..."], cwd=root),
        CheckSpec(name="go vet", primary=False, command=[go, "vet", "./..."], cwd=root),
    ]


def _rust_plan(root: str) -> list[CheckSpec]:
    cargo = shutil.which("cargo")
    if cargo is None:
        return [
            CheckSpec(
                name="cargo test",
                primary=True,
                command=None,
                cwd=root,
                unavailable_reason="cargo is configured (Cargo.toml present) but not installed",
            )
        ]
    return [
        CheckSpec(name="cargo test", primary=True, command=[cargo, "test"], cwd=root),
        CheckSpec(name="cargo check", primary=False, command=[cargo, "check"], cwd=root),
        CheckSpec(name="cargo clippy", primary=False, command=[cargo, "clippy"], cwd=root),
    ]


def _wrapper_or_tool(root: str, wrapper_name: str, tool_name: str) -> tuple[str | None, str | None]:
    """Prefer a checked-in wrapper script (`./mvnw`, `./gradlew`) over a
    globally installed tool — same "prefer checked-in wrappers" convention
    already used by `_python_command()` and this project's own
    `testing-and-verification` skill. Returns (executable, error) — exactly
    one is None."""
    wrapper = os.path.join(root, wrapper_name)
    if os.path.isfile(wrapper) and os.access(wrapper, os.X_OK):
        return wrapper, None
    tool = shutil.which(tool_name)
    if tool is not None:
        return tool, None
    return None, f"neither ./{wrapper_name} nor {tool_name} is available"


def _java_plan(detection: ProjectDetection) -> list[CheckSpec]:
    root = detection.root
    if detection.language == "java-maven":
        executable, error = _wrapper_or_tool(root, "mvnw", "mvn")
        name = "mvn test"
        manifest = "pom.xml"
    else:
        executable, error = _wrapper_or_tool(root, "gradlew", "gradle")
        name = "gradle test"
        manifest = "build.gradle"
    if executable is None:
        return [
            CheckSpec(
                name=name,
                primary=True,
                command=None,
                cwd=root,
                unavailable_reason=f"{manifest} is present but {error}",
            )
        ]
    return [CheckSpec(name=name, primary=True, command=[executable, "test"], cwd=root)]


def discover_verification_plan(detection: ProjectDetection) -> list[CheckSpec]:
    """Stage 2: given a detected project, decide which checks apply — pure
    (file reads and `shutil.which` lookups only, no subprocess execution).
    Never invents a check the project doesn't itself configure; an unknown
    project gets a single synthetic check explaining that plainly rather
    than a guessed command. An "ambiguous" detection (multiple candidate
    projects, no single obvious root — see `detect_project`) gets the same
    treatment: one synthetic, unavailable check naming every candidate,
    instead of silently verifying whichever one was found first.
    """
    if detection.language == "python":
        return _python_plan(detection.root)
    if detection.language == "node":
        return _node_plan(detection.root)
    if detection.language == "go":
        return _go_plan(detection.root)
    if detection.language == "rust":
        return _rust_plan(detection.root)
    if detection.language in ("java-maven", "java-gradle"):
        return _java_plan(detection)
    if detection.language == "ambiguous":
        candidates = ", ".join(
            f"{root} ({language})" for language, root in (detection.candidates or ())
        )
        return [
            CheckSpec(
                name="verification",
                primary=True,
                command=None,
                cwd=detection.root,
                unavailable_reason=(
                    "Multiple candidate projects were found with no single "
                    f"obvious root — verification is ambiguous: {candidates}. "
                    "Point HARNESS_WORKSPACE/--project at the specific "
                    "project to verify instead of the shared parent directory."
                ),
            )
        ]
    return [
        CheckSpec(
            name="verification",
            primary=True,
            command=None,
            cwd=detection.root,
            unavailable_reason=(
                "No known project type (Python, Node, Go, Rust, Java) was "
                "detected — verification is unavailable."
            ),
        )
    ]


def run_full_verification(working_dir: str) -> VerificationRun:
    """The full pipeline: detect → discover plan → execute every check →
    aggregate. This is the language-neutral entry point `runner.py`'s
    post-hoc verification loop calls; the agent-facing `run_tests` tool
    above reuses stages 1-3 directly for non-Python/Node languages instead
    of duplicating detection (see `RunTestsExecutor.__call__`).
    """
    detection = detect_project(working_dir)
    plan = discover_verification_plan(detection)
    return VerificationRun(checks=[execute_check(spec) for spec in plan])
