"""`harness-admin` — a maintenance CLI for the server-mode task store.

Talks directly to whatever backend `HARNESS_TASK_STORE` selects (via
`build_task_store(load_config())`), the same store `server.py` uses — this
is the CLI half of "delete all records for a project," alongside the REST
`DELETE /tasks`/`DELETE /tasks/{id}` endpoints (see ROADMAP.md's decisions
log for why both were built). Useful for a `memory`/`sqlite` deployment run
locally as well as a one-off maintenance script pointed at a shared
redis/mysql/postgres store.
"""

from __future__ import annotations

import argparse

from .config import load_config
from .task_store import build_task_store


def _cmd_delete_project(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = build_task_store(cfg)
    try:
        deleted = store.delete_by_project(args.project)
    finally:
        store.close()
    print(f"Deleted {deleted} task(s) for project {args.project!r}.")
    return 0


def _cmd_delete_task(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = build_task_store(cfg)
    try:
        deleted = store.delete(args.task_id)
    finally:
        store.close()
    if not deleted:
        print(f"No task found with id {args.task_id!r}.")
        return 1
    print(f"Deleted task {args.task_id!r}.")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    cfg = load_config()
    ttl_seconds = args.ttl_seconds if args.ttl_seconds is not None else cfg.task_ttl_seconds
    if ttl_seconds <= 0:
        print(
            "Refusing to purge: TTL is 0 (keep forever). Pass --ttl-seconds to purge "
            "records older than a specific age."
        )
        return 1
    store = build_task_store(cfg)
    try:
        purged = store.purge_expired(ttl_seconds)
    finally:
        store.close()
    print(f"Purged {purged} expired task(s) (ttl_seconds={ttl_seconds}).")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = build_task_store(cfg)
    try:
        record = store.get(args.task_id)
    finally:
        store.close()
    if record is None:
        print(f"No task found with id {args.task_id!r}.")
        return 1
    print(f"id:                 {record.id}")
    print(f"project:            {record.project}")
    print(f"status:             {record.status}")
    print(f"verification_state: {record.verification_state}")
    print(f"created_at:         {record.created_at}")
    print(f"updated_at:         {record.updated_at}")
    print(f"error:              {record.error}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-admin",
        description="Maintenance CLI for the harness server's task store "
        "(HARNESS_TASK_STORE-configured backend).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    delete_project = subparsers.add_parser(
        "delete-project",
        help="Delete every task record for a given project identifier.",
    )
    delete_project.add_argument("project", help="Project identifier (as passed to POST /tasks).")
    delete_project.set_defaults(func=_cmd_delete_project)

    delete_task = subparsers.add_parser("delete-task", help="Delete one task record by id.")
    delete_task.add_argument("task_id")
    delete_task.set_defaults(func=_cmd_delete_task)

    purge = subparsers.add_parser(
        "purge",
        help="Manually trigger a TTL-based purge of expired (completed/failed) records.",
    )
    purge.add_argument(
        "--ttl-seconds",
        type=int,
        default=None,
        help="Override HARNESS_TASK_TTL_SECONDS for this run. Records whose last update is "
        "older than this are deleted; a value <= 0 refuses to run (use the configured "
        "default instead of accidentally deleting everything).",
    )
    purge.set_defaults(func=_cmd_purge)

    show = subparsers.add_parser("show", help="Print one task record's summary.")
    show.add_argument("task_id")
    show.set_defaults(func=_cmd_show)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
