"""CLI entry point: `python -m harness "<task>"` (also installed as `harness`).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

from .config import ConfigError, load_config
from .runner import run_task


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Run a coding task through the agent harness.",
    )
    parser.add_argument("task", help="The task to give the agent.")
    parser.add_argument(
        "--execution",
        choices=("local", "docker"),
        default=None,
        help=(
            "Override HARNESS_EXECUTION for this run. Defaults to the value in "
            ".env (or 'local')."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.execution is not None:
        cfg = replace(cfg, execution=args.execution)

    if args.project is not None:
        project_dir = os.path.abspath(os.path.join(cfg.projects_dir, args.project))
        os.makedirs(project_dir, exist_ok=True)
        cfg = replace(cfg, workspace=project_dir)
        print(f"Project workspace: {cfg.workspace}")

    try:
        messages = run_task(args.task, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean CLI error, not a traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if messages:
        for content in messages[-1].content:
            text = getattr(content, "text", None)
            if text:
                print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
