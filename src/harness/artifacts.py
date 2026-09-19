"""Opt-in, per-run artifacts directory (`HARNESS_ARTIFACTS_DIR`) — a durable,
git-ignorable record of how a task run actually went: metadata, the full
message transcript, and token/cost metrics. `runner.py` is the only caller.

Deliberately a *separate*, sibling top-level directory (mirroring
`HARNESS_PROJECTS_DIR`'s own shape: one subfolder per project, `_unscoped`
when no `--project` was given), not a hidden folder nested inside each
generated project — this is harness-internal telemetry, not part of the
project a user might `git init` and commit. `MODEL_DECISIONS.md` stays
where it already is (inside the project workspace) — this module never
touches it. See MANUAL.md "Run artifacts" and ROADMAP.md's decisions log.

Pure file-writing, no SDK/`Conversation` dependency — `runner.py` extracts
`conversation.conversation_stats`-derived data into plain dicts before
calling `write_run_artifacts()`, keeping this module fully unit-testable
without a real conversation.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import ConfigError

_UNSCOPED_BUCKET = "_unscoped"


def resolve_run_artifacts_dir(artifacts_dir: str, project: str | None, run_id: str) -> str:
    """Resolve `<artifacts_dir>/<project-or-_unscoped>/<run_id>`, guaranteed
    to stay inside `artifacts_dir`.

    `project` gets the same validation `config.resolve_project_dir()`
    applies to `HARNESS_PROJECTS_DIR` (non-empty, relative, no `..`
    segment) plus a symlink-followed containment check — a separate
    implementation, not a shared call, since that function's own error
    messages name `HARNESS_PROJECTS_DIR` specifically, which would be
    actively wrong here (same "different root, different error context"
    call already made for `acceptance.py`'s path containment). `run_id` is
    never caller-supplied text (minted by the harness, or `server.py`'s own
    `TaskRecord.id`, already a UUID) so it needs no such check.
    """
    bucket = project if project else _UNSCOPED_BUCKET
    if bucket != _UNSCOPED_BUCKET:
        if bucket in (".", ".."):
            raise ConfigError(f"Invalid project name for artifacts: {bucket!r}")
        if os.path.isabs(bucket):
            raise ConfigError(f"Project name must be relative, not absolute: {bucket!r}")
        if any(part == ".." for part in Path(bucket).parts):
            raise ConfigError(f"Project name must not contain '..': {bucket!r}")

    artifacts_root = os.path.abspath(artifacts_dir)
    run_dir = os.path.join(artifacts_root, bucket, run_id)

    resolved_root = os.path.realpath(artifacts_root)
    resolved_run_dir = os.path.realpath(run_dir)
    if not Path(resolved_run_dir).is_relative_to(Path(resolved_root)):
        raise ConfigError(f"Artifacts path escapes HARNESS_ARTIFACTS_DIR: {bucket!r}")

    return run_dir


def write_run_artifacts(
    *,
    artifacts_dir: str,
    run_id: str,
    project: str | None,
    task: str,
    execution: str,
    model: str,
    model_selection: str,
    started_at: str,
    ended_at: str,
    messages: list[dict[str, Any]],
    outcome: Any | None,
    error: str | None,
    combined_metrics: dict[str, Any],
    per_model_metrics: dict[str, dict[str, Any]],
) -> None:
    """Write `metadata.json`/`transcript.json`/`metrics.json` for one run.

    `outcome`, if given, is `runner.TaskOutcome` — accessed structurally
    (`.verification_state`, `.completion_contract`, `.acceptance_results`)
    rather than imported, since `runner.py` already imports this module and
    importing `TaskOutcome` back here would be circular. `None` means the
    run raised before ever producing one — `metadata.json` still gets
    written, just without a verification verdict.
    """
    run_dir = resolve_run_artifacts_dir(artifacts_dir, project, run_id)
    os.makedirs(run_dir, exist_ok=True)

    metadata: dict[str, Any] = {
        "run_id": run_id,
        "project": project,
        "task": task,
        "execution": execution,
        "model": model,
        "model_selection": model_selection,
        "started_at": started_at,
        "ended_at": ended_at,
        "verification_state": outcome.verification_state if outcome is not None else None,
        "completion_contract": (
            asdict(outcome.completion_contract) if outcome is not None else None
        ),
        "acceptance_results": (
            [asdict(r) for r in outcome.acceptance_results]
            if outcome is not None and outcome.acceptance_results
            else None
        ),
        "error": error,
    }

    with open(os.path.join(run_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    with open(os.path.join(run_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)
    with open(os.path.join(run_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump({"combined": combined_metrics, "per_model": per_model_metrics}, f, indent=2)
