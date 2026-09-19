"""CLI entry point: `python -m harness "<task>"` (also installed as `harness`)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import replace
from urllib.parse import urlparse

from openhands.sdk.event import ActionEvent

from .acceptance import AcceptanceCheck, AcceptanceCheckError, parse_acceptance_checks
from .config import ConfigError, load_config, override_llm, resolve_project_dir
from .runner import run_task
from .skills import write_project_context

_URL_FETCH_TIMEOUT_SECONDS = 15


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _confirm_pending_actions(pending: Sequence[ActionEvent]) -> bool:
    """Interactive terminal handler for `HARNESS_CONFIRM_MODE=always`:
    prints each pending action and asks the user to approve or reject it.
    Any answer other than an explicit yes rejects — the safer default for
    a prompt nobody may be watching closely.
    """
    print("\n--- Confirmation required (HARNESS_CONFIRM_MODE=always) ---")
    for action in pending:
        print(f"  {action.tool_name}: {action.action}")
    answer = input("Approve? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _prompt_for_continuation(narrative: str) -> str | None:
    """Interactive terminal handler for `HARNESS_INTERACTIVE=yes`: prints
    the agent's latest reply and lets the user type a follow-up, or press
    Enter to accept it as done. The SDK can't tell a genuine clarifying
    question apart from real completion (see agent.py's `_AUTONOMOUS_SUFFIX`
    docstring), so this always offers the choice rather than guessing which
    one just happened.
    """
    if narrative:
        print(f"\n{narrative}\n")
    reply = input("Your reply (Enter to finish the task): ").strip()
    return reply or None


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


def resolve_acceptance_checks(value: str) -> list[AcceptanceCheck]:
    """Resolve the `--acceptance-checks` argument to parsed `AcceptanceCheck`
    objects — a JSON array of check definitions, either read from an
    existing local file or given inline (same "existing file wins over
    literal text" pattern as `resolve_task_source`, minus the URL-fetch
    case: an acceptance-check *list* has no legitimate reason to come from
    a remote URL the way a task description might).
    """
    if os.path.isfile(value):
        try:
            with open(value, encoding="utf-8") as f:
                raw = f.read()
        except OSError as exc:
            raise ValueError(f"Failed to read acceptance checks file {value!r}: {exc}") from exc
    else:
        raw = value

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--acceptance-checks must be valid JSON: {exc}") from exc
    if not isinstance(data, list):
        # ValueError, not TypeError (ruff's TRY004 suggestion): every error
        # this function raises must be a ValueError so main()'s single
        # `except ValueError` catches all of them uniformly.
        raise ValueError(  # noqa: TRY004
            "--acceptance-checks must be a JSON array of check objects."
        )

    try:
        return parse_acceptance_checks(data)
    except AcceptanceCheckError as exc:
        raise ValueError(str(exc)) from exc


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
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Allow the agent to pause and ask you a question instead of "
            "always pushing forward autonomously. When the initial run "
            "reaches a normal stop, you'll be prompted at the terminal to "
            "reply (continuing the task) or press Enter to accept it as "
            "done. Only the initial run is interactive — the automated "
            "task_tracker-completion and test-verification retry loops "
            "that follow still run fully autonomously, same as today. Off "
            "by default (current fully-autonomous behavior unchanged)."
        ),
    )
    parser.add_argument(
        "--require-verification",
        action="store_true",
        help=(
            "Treat an 'inconclusive' verification result (nothing runnable "
            "confirmed the software works — e.g. an unknown project type, a "
            "missing tool, or a project with no tests yet) as a failure: "
            "nonzero exit, same as retry_exhausted/timed_out/stuck/etc. Off "
            "by default — inconclusive exits 0, since nothing was proven "
            "broken, only unproven."
        ),
    )
    parser.add_argument(
        "--acceptance-checks",
        default=None,
        help=(
            "Optional, opt-in machine-checkable acceptance criteria: a JSON "
            "array of check objects, either given inline or as a path to a "
            "local JSON file. Each object is "
            '{"kind": "file_exists"|"file_contains", "path": "relative/path", '
            '"contains": "text" (file_contains only), "required": true '
            '(default), "description": "..."}. `path` is always resolved '
            "relative to the task's own workspace and cannot escape it "
            "(absolute paths and '..' are rejected). A failing required "
            "check downgrades an otherwise-'verified' result to "
            "'acceptance_failed'; a failing optional check is recorded but "
            "never blocks 'verified'."
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

    if args.interactive:
        cfg = replace(cfg, interactive=True)

    if args.agents_md is not None and args.project is None:
        print("Error: --agents-md requires --project", file=sys.stderr)
        return 1

    if args.project is not None:
        try:
            project_dir = resolve_project_dir(cfg.projects_dir, args.project)
        except ConfigError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1
        os.makedirs(project_dir, exist_ok=True)
        cfg = replace(cfg, workspace=project_dir)
        print(f"Project workspace: {cfg.workspace}")

    if args.agents_md is not None:
        write_project_context(cfg.workspace, args.agents_md)

    acceptance_checks: list[AcceptanceCheck] | None = None
    if args.acceptance_checks is not None:
        try:
            acceptance_checks = resolve_acceptance_checks(args.acceptance_checks)
        except ValueError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1

    on_confirm = _confirm_pending_actions if cfg.confirm_mode == "always" else None
    on_awaiting_input = _prompt_for_continuation if cfg.interactive else None
    try:
        messages = run_task(
            task,
            cfg=cfg,
            on_confirm=on_confirm,
            acceptance_checks=acceptance_checks,
            on_awaiting_input=on_awaiting_input,
        )
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
    # left incomplete, needed a confirm-mode approval nobody could answer,
    # ran out of its shared HARNESS_MAX_TASK_SECONDS budget, failed a
    # required --acceptance-checks criterion, or whose run never reached a
    # coherent finish, is a nonzero exit; "inconclusive" (nothing runnable
    # to check) is not an error but is still printed so it isn't mistaken
    # for a confirmed pass — unless --require-verification opted into
    # treating "nothing was checked" as a failure too (off by default: this
    # is a real behavior change a caller must ask for, not a silent default
    # flip — see ROADMAP.md).
    outcome = messages.outcome
    print(f"\nVerification: {outcome.verification_state}")
    for note in outcome.completion_contract.limitations:
        print(f"  - {note}")
    if outcome.verification_state in (
        "retry_exhausted",
        "no_progress",
        "timed_out",
        "incomplete",
        "confirmation_required",
        "budget_exhausted",
        "acceptance_failed",
        "stuck",
    ):
        return 1
    if args.require_verification and outcome.verification_state == "inconclusive":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
