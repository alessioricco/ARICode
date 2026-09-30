"""`harness-admin` — maintenance CLI: server-mode task store + third-party skills.

The `skills` subcommand group manages local-only third-party skills (see
third_party_skills.py and MANUAL.md "Third-party skills").

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

from . import third_party_skills as tps
from .config import Config, load_config
from .skills import load_skill_catalog
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


# --- Third-party skills --------------------------------------------------------


def _skill_paths(cfg: Config) -> dict:
    return {"install_dir": cfg.third_party_skills_dir, "lock_path": cfg.third_party_skills_lock}


def _print_provenance(name: str, entry: tps.LockEntry) -> None:
    print(f"skill:     {name}")
    print(f"source:    {entry.source} ({entry.path})")
    print(f"revision:  {entry.revision}")
    print(f"url:       {entry.source_url}")
    print(
        f"license:   {entry.license}" + ("" if entry.license_established else " (NOT established)")
    )
    if entry.license_url:
        print(f"license:   {entry.license_url}")
    for f in entry.license_files:
        print(f"  preserved: {f}")
    for note in entry.license_notes:
        print(f"  note: {note}")
    print(f"integrity: {entry.integrity}")
    print(f"enabled:   {'yes' if entry.enabled else 'no'}")


def _skills_cmd(func):
    """Run a skills subcommand, turning a ThirdPartySkillError into exit 1."""

    def wrapper(args: argparse.Namespace) -> int:
        try:
            return func(args, load_config())
        except tps.ThirdPartySkillError as exc:
            print(f"Error: {exc}")
            return 1

    return wrapper


@_skills_cmd
def _cmd_skills_install(args: argparse.Namespace, cfg: Config) -> int:
    reserved = {s.name for s in load_skill_catalog(cfg.skills_dir)}
    result = tps.install(
        args.source, args.path, ref=args.ref, reserved_names=reserved, **_skill_paths(cfg)
    )
    _print_provenance(result.name, result.entry)
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(
        f"\nInstalled but NOT enabled. Review the files in "
        f"{cfg.third_party_skills_dir}/{result.name}/ and the upstream license, then run: "
        f"harness-admin skills enable {result.name}"
    )
    print(
        "The detected license is a text heuristic, not legal advice; downloading or "
        "gitignoring a skill does not by itself grant permission to use it."
    )
    return 0


@_skills_cmd
def _cmd_skills_list(args: argparse.Namespace, cfg: Config) -> int:
    rows = tps.list_installed(**_skill_paths(cfg))
    if not rows:
        print("No third-party skills installed.")
        return 0
    for name, entry, status in rows:
        state = "enabled" if entry.enabled else "disabled"
        print(
            f"{name:<28} {state:<9} {entry.license:<13} {entry.revision[:12]}  "
            f"files={status}  {entry.source} ({entry.path})"
        )
    return 0


@_skills_cmd
def _cmd_skills_enable(args: argparse.Namespace, cfg: Config) -> int:
    entry = tps.set_enabled(
        args.name,
        True,
        accept_unknown_license=args.accept_unknown_license,
        **_skill_paths(cfg),
    )
    _print_provenance(args.name, entry)
    print(f"\nEnabled {args.name!r}: its instructions will be passed to the agent.")
    return 0


@_skills_cmd
def _cmd_skills_disable(args: argparse.Namespace, cfg: Config) -> int:
    tps.set_enabled(args.name, False, **_skill_paths(cfg))
    print(f"Disabled {args.name!r}; it will no longer be passed to the agent.")
    return 0


@_skills_cmd
def _cmd_skills_update(args: argparse.Namespace, cfg: Config) -> int:
    entry, reapproval = tps.update(args.name, ref=args.ref, **_skill_paths(cfg))
    _print_provenance(args.name, entry)
    if reapproval:
        print(
            f"\nContent or license changed upstream: {args.name!r} was DISABLED. Review it, "
            f"then run: harness-admin skills enable {args.name}"
        )
    return 0


@_skills_cmd
def _cmd_skills_remove(args: argparse.Namespace, cfg: Config) -> int:
    tps.remove(args.name, **_skill_paths(cfg))
    print(f"Removed {args.name!r} (files and lockfile entry).")
    return 0


@_skills_cmd
def _cmd_skills_sync(args: argparse.Namespace, cfg: Config) -> int:
    results = tps.sync(**_skill_paths(cfg))
    for name, status in results:
        print(f"{name:<28} {status}")
    return 0 if all(status in ("ok", "restored") for _, status in results) else 1


def _add_skills_parser(subparsers) -> None:
    skills = subparsers.add_parser(
        "skills",
        help="Manage local-only third-party skills (installed != enabled).",
    )
    sub = skills.add_subparsers(dest="skills_command", required=True)

    install = sub.add_parser("install", help="Fetch one upstream SKILL.md directory (disabled).")
    install.add_argument("source", help="'github:owner/repo' or an https:// git URL.")
    install.add_argument("path", help="Skill directory within the repository, e.g. skills/pdf.")
    install.add_argument("--ref", default=None, help="Branch, tag, or commit (default: HEAD).")
    install.set_defaults(func=_cmd_skills_install)

    sub.add_parser("list", help="List installed skills with state/license/revision.").set_defaults(
        func=_cmd_skills_list
    )

    enable = sub.add_parser("enable", help="Pass a skill to the agent.")
    enable.add_argument("name")
    enable.add_argument(
        "--accept-unknown-license",
        action="store_true",
        help="Required to enable a skill whose license could not be established.",
    )
    enable.set_defaults(func=_cmd_skills_enable)

    disable = sub.add_parser("disable", help="Stop passing a skill to the agent.")
    disable.add_argument("name")
    disable.set_defaults(func=_cmd_skills_disable)

    upd = sub.add_parser(
        "update", help="Re-fetch; any content/license change disables the skill again."
    )
    upd.add_argument("name")
    upd.add_argument("--ref", default=None, help="Default: the ref originally requested.")
    upd.set_defaults(func=_cmd_skills_update)

    rm = sub.add_parser("remove", help="Delete a skill's files and lockfile entry.")
    rm.add_argument("name")
    rm.set_defaults(func=_cmd_skills_remove)

    sub.add_parser(
        "sync", help="Restore locked skills at their pinned revisions; verify integrity."
    ).set_defaults(func=_cmd_skills_sync)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-admin",
        description="Maintenance CLI for the harness server's task store "
        "(HARNESS_TASK_STORE-configured backend) and local third-party skills.",
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

    _add_skills_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
