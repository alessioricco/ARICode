"""CLI entry point: `python -m harness "<task>"` (also installed as `harness`)."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from dataclasses import replace
from urllib.parse import urlparse

from .config import ConfigError, load_config, override_llm
from .runner import run_task
from .skills import write_project_context

_URL_FETCH_TIMEOUT_SECONDS = 15


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_task_source(value: str) -> str:
    """Resolve the `task` argument to actual task text.

    Checked in order: an http(s) URL is fetched (the scheme check is also
    what keeps a bare `file://...` value from ever reaching urlopen); else an
    existing local file is read; else the value is used as literal task
    text, unchanged.
    """
    if _looks_like_url(value):
        try:
            with urllib.request.urlopen(value, timeout=_URL_FETCH_TIMEOUT_SECONDS) as resp:
                content = resp.read().decode("utf-8")
        except (urllib.error.URLError, UnicodeDecodeError) as exc:
            raise ValueError(f"Failed to fetch task from URL {value!r}: {exc}") from exc
    elif os.path.isfile(value):
        try:
            with open(value, encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            raise ValueError(f"Failed to read task file {value!r}: {exc}") from exc
    else:
        return value

    content = content.strip()
    if not content:
        raise ValueError(f"Task content from {value!r} is empty")
    return content


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Run a coding task through the agent harness.",
    )
    parser.add_argument(
        "task",
        help=(
            "The task to give the agent — literal text, a path to a local "
            "file containing it, or an http(s) URL to fetch it from."
        ),
    )
    parser.add_argument(
        "--execution",
        choices=("local", "docker"),
        default=None,
        help=(
            "Override HARNESS_EXECUTION for this run. Defaults to the value in .env (or 'local')."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Override LLM_MODEL for this run only, without touching .env — "
            "e.g. to compare how two models handle the same task."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Override LLM_API_KEY for this run only (pairs with --model when swapping providers).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override LLM_BASE_URL for this run only (e.g. to point at a local model endpoint).",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help=(
            "Override LLM_REASONING_EFFORT for this run only, without touching "
            ".env — e.g. to compare how the same model behaves at different "
            "effort levels. Common values: none | minimal | low | medium | "
            "high | xhigh | max (provider-neutral; leave unset for the SDK's "
            "own default)."
        ),
    )
    parser.add_argument(
        "--project",
        default=None,
        help=(
            "Project name. The agent's workspace becomes "
            "<HARNESS_PROJECTS_DIR>/<project> (created if missing), so each "
            "project's generated software lands in its own subfolder. "
            "Defaults to HARNESS_WORKSPACE when omitted."
        ),
    )
    parser.add_argument(
        "--agents-md",
        default=None,
        help=(
            "Content to write as this project's AGENTS.md before running the "
            "task — persistent, project-specific facts/conventions the agent "
            "picks up on this and every future task against the same "
            "project. Requires --project (there's no project directory to "
            "write into otherwise)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        task = resolve_task_source(args.task)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
        if (
            args.model is not None
            or args.api_key is not None
            or args.base_url is not None
            or args.reasoning_effort is not None
        ):
            cfg = override_llm(
                cfg,
                model=args.model,
                api_key=args.api_key,
                base_url=args.base_url,
                reasoning_effort=args.reasoning_effort,
            )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.execution is not None:
        cfg = replace(cfg, execution=args.execution)

    if args.agents_md is not None and args.project is None:
        print("Error: --agents-md requires --project", file=sys.stderr)
        return 1

    if args.project is not None:
        project_dir = os.path.abspath(os.path.join(cfg.projects_dir, args.project))
        os.makedirs(project_dir, exist_ok=True)
        cfg = replace(cfg, workspace=project_dir)
        print(f"Project workspace: {cfg.workspace}")

    if args.agents_md is not None:
        write_project_context(cfg.workspace, args.agents_md)

    try:
        messages = run_task(task, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean CLI error, not a traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if messages:
        for content in messages[-1].content:
            text = getattr(content, "text", None)
            if text:
                print(text)

    # Make the terminal outcome explicit rather than letting the agent's own
    # (possibly optimistic) last message stand as the only signal — see
    # runner.py's TaskOutcome / MANUAL.md "Test verification". A task whose
    # verification failed and couldn't be fixed, made no observable progress
    # on a fix attempt, kept timing out, whose own task_tracker list was
    # left incomplete, or whose run never reached a coherent finish, is a
    # nonzero exit; "inconclusive" (nothing runnable to check) is not an
    # error but is still printed so it isn't mistaken for a confirmed pass.
    outcome = messages.outcome
    print(f"\nVerification: {outcome.verification_state}")
    for note in outcome.completion_contract.limitations:
        print(f"  - {note}")
    if outcome.verification_state in (
        "retry_exhausted",
        "no_progress",
        "timed_out",
        "incomplete",
        "stuck",
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
