"""CLI entry point: `python -m harness "<task>"` (also installed as `harness`).
"""

from __future__ import annotations

import argparse
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

    if cfg.execution == "docker":
        print(
            "HARNESS_EXECUTION=docker is not implemented yet "
            "(see docs/SPEC.md section 9 — Docker execution is optional and "
            "not yet built). Use 'local' for now.",
            file=sys.stderr,
        )
        return 1

    messages = run_task(args.task, cfg=cfg)
    if messages:
        for content in messages[-1].content:
            text = getattr(content, "text", None)
            if text:
                print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
