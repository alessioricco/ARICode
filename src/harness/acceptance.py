"""Optional, caller-supplied machine-checkable acceptance criteria.

Distinct from `custom_tools/run_tests_tool.py`'s project-health checks
(tests/lint/build, inferred from project metadata): these are checks the
*caller* explicitly supplies for one specific task ("does the file this
task asked for exist", "does it contain what was asked"), evaluated by the
harness itself after the run, blind to whatever the agent actually did.
Never agent-facing and never agent-defined — the agent has no tool to add
or see these; only `cli.py`/`server.py` accept them from the caller.

Deliberately scoped to two check kinds only — `file_exists` and
`file_contains` — with no arbitrary-command execution kind, even though
"commands" was one of the kinds originally proposed. See ROADMAP.md's
decisions log for why: a caller-supplied "run this command" check would be
a new, harness-triggered remote-code-execution surface, compounding
directly with server mode's lack of authentication (see `todo.md`'s
server-mode-auth item) in a way a caller-supplied file *path* check does
not once properly contained to the task's own workspace (the same
containment problem, and the same fix shape, as `config.resolve_project_dir`
— kept as a separate implementation here rather than a shared one, since
the two have different roots and different error-message context).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CHECK_KINDS = ("file_exists", "file_contains")

# A "does this file contain X" check has no legitimate reason to need more
# than this much of a file — caps memory/CPU cost against a caller pointing
# a check at an unexpectedly huge file.
_MAX_FILE_READ_BYTES = 1_000_000


class AcceptanceCheckError(ValueError):
    """Raised for a malformed acceptance-check definition or a path that
    escapes the task's workspace. A `ValueError` subclass so `server.py`'s
    existing `except (ConfigError, ValueError)` blocks catch it with no
    changes needed. Message is user-facing.
    """


@dataclass(frozen=True)
class AcceptanceCheck:
    kind: str  # one of CHECK_KINDS
    path: str  # relative to the task's workspace — never absolute, never '..'
    contains: str | None = None  # required for "file_contains"
    required: bool = True  # a failing optional check never blocks "verified"
    description: str | None = None  # human-readable label for reports


@dataclass(frozen=True)
class AcceptanceCheckResult:
    check: AcceptanceCheck
    passed: bool
    detail: str


def parse_acceptance_check(data: Mapping[str, object]) -> AcceptanceCheck:
    """Validate one raw (e.g. JSON-decoded) acceptance-check definition.

    Raises `AcceptanceCheckError` naming exactly what's wrong — this is
    caller input (a CLI flag or an API request field), so the message must
    be specific enough to fix without reading source.
    """
    if not isinstance(data, Mapping):
        raise AcceptanceCheckError(
            f"Each acceptance check must be a JSON object, got {type(data).__name__}."
        )

    kind = data.get("kind")
    if kind not in CHECK_KINDS:
        allowed = " | ".join(CHECK_KINDS)
        raise AcceptanceCheckError(
            f"Unknown acceptance check kind {kind!r}; must be one of: {allowed}."
        )

    path = data.get("path")
    if not isinstance(path, str) or not path.strip():
        raise AcceptanceCheckError(
            "Acceptance check 'path' is required and must be a non-empty string."
        )

    contains = data.get("contains")
    if kind == "file_contains":
        if not isinstance(contains, str) or not contains:
            raise AcceptanceCheckError(
                "Acceptance check kind 'file_contains' requires a non-empty 'contains' string."
            )
    elif contains is not None:
        raise AcceptanceCheckError(f"'contains' is not valid for acceptance check kind {kind!r}.")

    required = data.get("required", True)
    if not isinstance(required, bool):
        raise AcceptanceCheckError("Acceptance check 'required' must be a boolean.")

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise AcceptanceCheckError("Acceptance check 'description' must be a string.")

    return AcceptanceCheck(
        kind=kind, path=path, contains=contains, required=required, description=description
    )


def parse_acceptance_checks(items: Sequence[Mapping[str, object]]) -> list[AcceptanceCheck]:
    return [parse_acceptance_check(item) for item in items]


def _resolve_checked_path(workspace: str, path: str) -> str:
    """Resolve an acceptance check's caller-supplied `path` to a location
    guaranteed to stay inside the task's own workspace.

    Mirrors `config.resolve_project_dir`'s containment logic exactly
    (reject absolute/`..` outright, then a symlink-followed containment
    check via `os.path.realpath`) but is its own implementation rather
    than a shared one — see this module's docstring for why. Without
    this, an unauthenticated caller (server mode has no auth) could ask
    "does /etc/passwd contain 'root'" as an acceptance check and learn
    about arbitrary host files' existence and contents.
    """
    if not path or path in (".", ".."):
        raise AcceptanceCheckError(f"Invalid acceptance check path: {path!r}")
    if os.path.isabs(path):
        raise AcceptanceCheckError(
            f"Acceptance check path must be relative, not absolute: {path!r}"
        )
    if any(part == ".." for part in Path(path).parts):
        raise AcceptanceCheckError(f"Acceptance check path must not contain '..': {path!r}")

    workspace_root = os.path.abspath(workspace)
    candidate = os.path.join(workspace_root, path)

    resolved_root = os.path.realpath(workspace_root)
    resolved_candidate = os.path.realpath(candidate)
    if not Path(resolved_candidate).is_relative_to(Path(resolved_root)):
        raise AcceptanceCheckError(f"Acceptance check path escapes the task workspace: {path!r}")

    return candidate


def _read_capped(path: str) -> tuple[str, bool]:
    """Read up to `_MAX_FILE_READ_BYTES` of `path` as text. Returns
    `(text, truncated)` — `truncated` is True if the file was larger than
    the cap, so callers can say so rather than silently checking only a
    prefix with no indication.
    """
    with open(path, "rb") as f:
        raw = f.read(_MAX_FILE_READ_BYTES + 1)
    truncated = len(raw) > _MAX_FILE_READ_BYTES
    return raw[:_MAX_FILE_READ_BYTES].decode("utf-8", errors="replace"), truncated


def evaluate_acceptance_check(workspace: str, check: AcceptanceCheck) -> AcceptanceCheckResult:
    try:
        resolved = _resolve_checked_path(workspace, check.path)
    except AcceptanceCheckError as exc:
        return AcceptanceCheckResult(check=check, passed=False, detail=str(exc))

    if check.kind == "file_exists":
        exists = os.path.isfile(resolved)
        detail = f"{check.path} exists" if exists else f"{check.path} does not exist"
        return AcceptanceCheckResult(check=check, passed=exists, detail=detail)

    # kind == "file_contains" (the only other value parse_acceptance_check allows)
    if not os.path.isfile(resolved):
        return AcceptanceCheckResult(
            check=check, passed=False, detail=f"{check.path} does not exist"
        )
    try:
        content, truncated = _read_capped(resolved)
    except OSError as exc:
        return AcceptanceCheckResult(
            check=check, passed=False, detail=f"could not read {check.path}: {exc}"
        )
    note = " (checked only the first 1MB)" if truncated else ""
    found = check.contains in content
    detail = (
        f"{check.path} contains the expected text{note}"
        if found
        else f"{check.path} does not contain the expected text{note}"
    )
    return AcceptanceCheckResult(check=check, passed=found, detail=detail)


def evaluate_acceptance_checks(
    workspace: str, checks: Sequence[AcceptanceCheck]
) -> list[AcceptanceCheckResult]:
    return [evaluate_acceptance_check(workspace, check) for check in checks]
