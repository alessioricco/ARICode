"""Local-only, license-aware management of third-party AgentSkills.

Kept deliberately separate from the tracked first-party catalog
(`skills.py` / `HARNESS_SKILLS_DIR`):

- Downloaded files live in `HARNESS_THIRD_PARTY_SKILLS_DIR` (gitignored),
  one `SKILL.md` directory per skill, exactly one level deep — the only
  depth the SDK's `find_skill_md_directories()` detects.
- Provenance and enablement live in a JSON lockfile
  (`HARNESS_THIRD_PARTY_SKILLS_LOCK`): upstream repo, skill path, immutable
  commit, detected license, source/license URLs, and a content hash. Never
  skill content itself.
- Installed is not enabled. Only entries explicitly enabled — and, when the
  license could not be established, explicitly acknowledged — whose on-disk
  files still match the recorded hash are passed to the agent.

Reuses the SDK's own git fetch (`fetch_skill_with_resolution`: subpath
containment, resolved commit SHA) and `Skill.load`, but *not* its
`InstallationManager`: that enables new installs by default and auto-enables
any untracked directory it discovers, both contrary to the explicit-opt-in
rule here (see ROADMAP.md's decisions log).

Skill content is treated as untrusted: this module never executes a
downloaded file, loads with `skip_mcp=True` (a skill's `.mcp.json` would
otherwise start MCP servers), and escapes inline ``!`cmd` `` snippets that
the SDK's `invoke_skill` tool would otherwise run in a shell.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import yaml
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.skills import Skill
from openhands.sdk.skills.exceptions import SkillError
from openhands.sdk.skills.fetch import SkillFetchError, fetch_skill_with_resolution
from openhands.sdk.skills.utils import find_skill_md

logger = logging.getLogger(__name__)

# What the SDK's own load_skills_from_dir() treats as "this skill failed to load".
_SKILL_LOAD_ERRORS = (SkillError, OSError, yaml.YAMLError, ValueError)

LOCK_VERSION = 1
UNKNOWN_LICENSE = "unknown"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_SHORTHAND_RE = re.compile(r"^github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
# Files that establish a license vs. files that only carry attribution; both
# kinds are preserved on install, only the former are used for detection.
_LICENSE_STEMS = ("LICENSE", "LICENCE", "COPYING")
_ATTRIBUTION_STEMS = (*_LICENSE_STEMS, "NOTICE", "AUTHORS", "COPYRIGHT")
UPSTREAM_ATTRIBUTION_DIR = ".upstream"
# `!`cmd`` not already escaped — see openhands/sdk/skills/execute.py.
_INLINE_COMMAND_RE = re.compile(r"(?<!\\)!`")

# Deliberately small, conservative text fingerprints: anything unmatched,
# ambiguous, or conflicting resolves to "unknown", never to a guess.
# (identifier, phrases that must all appear, phrases that must not appear)
_LICENSE_FINGERPRINTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Apache-2.0", ("apache license", "version 2.0"), ()),
    ("MPL-2.0", ("mozilla public license", "version 2.0"), ()),
    (
        "MIT",
        (
            "permission is hereby granted, free of charge",
            "the above copyright notice and this permission notice shall be included",
        ),
        (),
    ),
    (
        "ISC",
        ("permission to use, copy, modify, and/or distribute this software for any purpose",),
        (),
    ),
    (
        "BSD-3-Clause",
        ("redistribution and use in source and binary forms", "neither the name"),
        (),
    ),
    (
        "BSD-2-Clause",
        ("redistribution and use in source and binary forms",),
        ("neither the name",),
    ),
    ("Unlicense", ("this is free and unencumbered software released into the public domain",), ()),
    ("CC0-1.0", ("cc0 1.0 universal",), ()),
)
KNOWN_LICENSES = frozenset(ident for ident, _, _ in _LICENSE_FINGERPRINTS)


class ThirdPartySkillError(Exception):
    """User-facing failure of a third-party skill operation."""


@dataclass
class LockEntry:
    """One installed skill's provenance and enablement — never its content."""

    source: str  # normalized https clone URL
    path: str  # skill directory within the upstream repository
    revision: str  # full 40-char commit SHA actually installed
    requested_ref: str | None
    license: str  # SPDX-style identifier, or UNKNOWN_LICENSE
    license_files: list[str]  # relative to the installed skill directory
    license_notes: list[str]
    license_url: str | None
    source_url: str
    integrity: str  # "sha256-<hex>" over every installed file
    enabled: bool = False
    license_acknowledged: bool = False

    @property
    def license_established(self) -> bool:
        return self.license != UNKNOWN_LICENSE


@dataclass
class InstallResult:
    name: str
    entry: LockEntry
    warnings: list[str] = field(default_factory=list)


# --- Lockfile --------------------------------------------------------------


def read_lock(lock_path: str | Path) -> dict[str, LockEntry]:
    """Read the lockfile; a missing file means no third-party skills."""
    lock_path = Path(lock_path)
    if not lock_path.exists():
        return {}
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        if data.get("version") != LOCK_VERSION:
            raise ValueError(f"unsupported lockfile version {data.get('version')!r}")
        return {name: LockEntry(**raw) for name, raw in data["skills"].items()}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ThirdPartySkillError(f"Malformed third-party skills lockfile {lock_path}: {exc}")


def write_lock(lock_path: str | Path, entries: dict[str, LockEntry]) -> None:
    """Write the lockfile atomically, with stable key order for clean diffs."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": LOCK_VERSION,
        "skills": {name: asdict(entries[name]) for name in sorted(entries)},
    }
    fd, tmp = tempfile.mkstemp(dir=lock_path.parent, prefix=".lock-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, lock_path)


# --- Source validation ------------------------------------------------------


def parse_source(source: str) -> tuple[str, str | None]:
    """Return `(clone_url, github_web_base_or_None)`.

    Only `github:owner/repo` and credential-free `https://` URLs are accepted
    — no local paths, SSH, or plain http, so a fetch can only ever reach the
    single named upstream over an authenticated-by-host channel, and no
    credential ever lands in the lockfile.
    """
    source = source.strip()
    match = _GITHUB_SHORTHAND_RE.match(source)
    if match:
        owner, repo = match.groups()
        repo = repo.removesuffix(".git")
        return f"https://github.com/{owner}/{repo}.git", f"https://github.com/{owner}/{repo}"
    parsed = urlparse(source)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ThirdPartySkillError(
            f"Unsupported source {source!r}: use 'github:owner/repo' or an https:// git URL."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ThirdPartySkillError(
            "Source URL must not contain credentials, a query string, or a fragment."
        )
    repo_path = parsed.path.strip("/").removesuffix(".git")
    if not repo_path:
        raise ThirdPartySkillError(f"Source URL {source!r} names no repository.")
    host = parsed.hostname.lower()
    clone_url = f"https://{parsed.netloc.lower()}/{repo_path}.git"
    web_base = f"https://github.com/{repo_path}" if host == "github.com" else None
    return clone_url, web_base


def validate_skill_path(skill_path: str) -> str:
    """Normalize a repo-relative skill directory path; reject escapes."""
    if "\\" in skill_path:
        raise ThirdPartySkillError("Skill path must use forward slashes.")
    raw = skill_path.strip()
    path = PurePosixPath(raw.rstrip("/"))
    if raw.startswith("/") or str(path) in ("", ".") or ".." in path.parts:
        raise ThirdPartySkillError(
            f"Skill path {skill_path!r} must be a relative directory inside the repository "
            "(no '..', not the repository root)."
        )
    return str(path)


def _check_name(name: str) -> None:
    try:
        validate_extension_name(name)
    except ValueError as exc:
        raise ThirdPartySkillError(str(exc)) from exc


# --- Integrity, copying, license detection ----------------------------------


def compute_integrity(root: Path) -> str:
    """Hash every file under `root` (relative path + content). Symlinks are refused."""
    digest = hashlib.sha256()
    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ThirdPartySkillError(f"Refusing symlink in skill directory: {path}")
        if path.is_file():
            files.append((path.relative_to(root).as_posix(), path))
    for rel, path in sorted(files):
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return f"sha256-{digest.hexdigest()}"


def integrity_status(skill_dir: Path, entry: LockEntry) -> str:
    """`ok` | `missing` | `modified` for an installed skill vs. its lock entry."""
    if not skill_dir.is_dir():
        return "missing"
    try:
        actual = compute_integrity(skill_dir)
    except ThirdPartySkillError:
        return "modified"
    return "ok" if actual == entry.integrity else "modified"


def _copy_regular_files(src: Path, dst: Path) -> None:
    """Copy regular files only (no symlinks, no `.git`, no exec bits)."""
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        current = Path(dirpath)
        for name in (*dirnames, *filenames):
            if (current / name).is_symlink():
                raise ThirdPartySkillError(
                    f"Upstream skill contains a symlink ({(current / name).relative_to(src)}); "
                    "refusing to install."
                )
        target_dir = dst / current.relative_to(src)
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            shutil.copyfile(current / name, target_dir / name)


def _matches_stem(path: Path, stems: Iterable[str]) -> bool:
    upper = path.name.upper()
    return any(upper == s or upper.startswith((s + ".", s + "-", s + "_")) for s in stems)


def detect_license_text(text: str) -> str | None:
    """Identify a license from its text; `None` if unmatched or ambiguous."""
    normalized = " ".join(text.lower().split())
    matches = [
        ident
        for ident, required, forbidden in _LICENSE_FINGERPRINTS
        if all(p in normalized for p in required) and not any(p in normalized for p in forbidden)
    ]
    return matches[0] if len(matches) == 1 else None


def _detect_from_files(files: list[Path]) -> str | None:
    found = {detect_license_text(f.read_text(encoding="utf-8", errors="replace")) for f in files}
    return found.pop() if len(found) == 1 else None


def _determine_license(
    staged: Path, declared: str | None, skill_path: str, web_base: str | None, revision: str
) -> tuple[str, list[str], list[str], str | None]:
    """Return `(identifier, license_files, notes, license_url)`.

    Precedence: a license file in the skill directory itself, else one at the
    repository root (copied into `.upstream/`). Anything that doesn't resolve
    to exactly one recognized identifier — no file, unrecognized text,
    conflicting files, or a frontmatter declaration contradicting the file —
    is `unknown`, which blocks enablement until explicitly acknowledged.
    """
    notes: list[str] = []
    skill_files = sorted(
        p for p in staged.iterdir() if p.is_file() and _matches_stem(p, _LICENSE_STEMS)
    )
    upstream_dir = staged / UPSTREAM_ATTRIBUTION_DIR
    root_files = (
        sorted(p for p in upstream_dir.iterdir() if _matches_stem(p, _LICENSE_STEMS))
        if upstream_dir.is_dir()
        else []
    )
    declared_known = None
    if declared:
        declared_known = next((k for k in KNOWN_LICENSES if k.lower() == declared.lower()), None)
        if declared_known is None:
            notes.append(f"SKILL.md frontmatter declares license {declared!r} (not an identifier).")

    if skill_files:
        evidence, repo_rel = skill_files, f"{skill_path}/{skill_files[0].name}"
    elif root_files:
        evidence, repo_rel = root_files, root_files[0].name
        notes.append(
            "License taken from the repository root; the skill directory has no license file "
            "of its own, so confirm the root license covers this path."
        )
    else:
        evidence, repo_rel = [], None
        notes.append("No license file found in the skill directory or repository root.")

    identifier = _detect_from_files(evidence) if evidence else None
    if evidence and identifier is None:
        notes.append("License text was not recognized, or multiple license files disagree.")
    if identifier and declared_known and declared_known != identifier:
        notes.append(
            f"SKILL.md declares {declared_known} but the license file reads as {identifier}."
        )
        identifier = None
    if not evidence and declared_known:
        notes.append(f"SKILL.md declares {declared_known}, but no license text is present.")

    license_files = [p.relative_to(staged).as_posix() for p in (*skill_files, *root_files)]
    license_url = (
        f"{web_base}/blob/{revision}/{repo_rel}" if web_base and repo_rel is not None else None
    )
    return identifier or UNKNOWN_LICENSE, license_files, notes, license_url


def neutralize_inline_commands(content: str) -> str:
    """Escape every ``!`cmd` `` so the SDK renders it as literal text.

    The SDK's `invoke_skill` tool runs `render_content_with_commands()` on a
    skill's body, executing each unescaped ``!`cmd` `` in a shell with full
    process privileges; its own documented escape is a leading backslash.
    """
    return _INLINE_COMMAND_RE.sub(r"\\!`", content)


# --- Fetch + stage -----------------------------------------------------------


@dataclass
class _Staged:
    dir: Path  # <staging tmp>/<skill name>/, ready to move into place
    name: str
    revision: str
    entry: LockEntry
    warnings: list[str]


def _fetch_and_stage(
    clone_url: str,
    web_base: str | None,
    skill_path: str,
    ref: str | None,
    install_dir: Path,
    staging_root: Path,
) -> _Staged:
    try:
        fetched, revision = fetch_skill_with_resolution(
            source=clone_url,
            cache_dir=install_dir / ".cache",
            ref=ref,
            update=True,
            repo_path=skill_path,
        )
    except SkillFetchError as exc:
        cause = exc.__cause__ or exc
        raise ThirdPartySkillError(f"Could not fetch {clone_url} ({skill_path}): {cause}") from exc
    if not revision or not _SHA_RE.match(revision):
        raise ThirdPartySkillError(
            f"Could not resolve an immutable commit for {clone_url} (got {revision!r})."
        )
    fetched = Path(fetched)
    if find_skill_md(fetched) is None:
        raise ThirdPartySkillError(
            f"{skill_path!r} at {revision[:12]} has no SKILL.md; only AgentSkills-format "
            "skill directories are supported."
        )
    repo_root = fetched
    for _ in PurePosixPath(skill_path).parts:
        repo_root = repo_root.parent

    # Stage under the upstream directory's own name: strict SKILL.md loading
    # requires the frontmatter name to match its directory name.
    staged = staging_root / fetched.name
    _copy_regular_files(fetched, staged)
    root_attribution = [
        p
        for p in sorted(repo_root.iterdir())
        if p.is_file() and not p.is_symlink() and _matches_stem(p, _ATTRIBUTION_STEMS)
    ]
    if root_attribution:
        (staged / UPSTREAM_ATTRIBUTION_DIR).mkdir(exist_ok=True)
        for p in root_attribution:
            shutil.copyfile(p, staged / UPSTREAM_ATTRIBUTION_DIR / p.name)

    try:
        skill = Skill.load(find_skill_md(staged), strict=True, skip_mcp=True)
    except _SKILL_LOAD_ERRORS as exc:
        raise ThirdPartySkillError(f"Upstream SKILL.md failed validation: {exc}") from exc
    _check_name(skill.name)

    identifier, license_files, notes, license_url = _determine_license(
        staged, skill.license, skill_path, web_base, revision
    )
    warnings: list[str] = []
    if _INLINE_COMMAND_RE.search(skill.content):
        warnings.append(
            "SKILL.md contains inline !`command` syntax; the harness escapes it on load so it "
            "is shown as text, never executed."
        )
    if (staged / ".mcp.json").exists():
        warnings.append("Skill ships a .mcp.json; the harness ignores it (no MCP servers started).")
    if any((staged / d).is_dir() for d in ("scripts", "bin")):
        warnings.append(
            "Skill ships scripts; the harness never runs them, but an enabled skill's "
            "instructions may ask the agent to — review them before enabling."
        )
    source_url = f"{web_base}/tree/{revision}/{skill_path}" if web_base else clone_url
    entry = LockEntry(
        source=clone_url,
        path=skill_path,
        revision=revision,
        requested_ref=ref,
        license=identifier,
        license_files=license_files,
        license_notes=notes,
        license_url=license_url,
        source_url=source_url,
        integrity=compute_integrity(staged),
    )
    return _Staged(dir=staged, name=skill.name, revision=revision, entry=entry, warnings=warnings)


def _place(staged: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(dest))


def _staging_root(install_dir: Path) -> tempfile.TemporaryDirectory:
    (install_dir / ".staging").mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=install_dir / ".staging")


def _require(entries: dict[str, LockEntry], name: str) -> LockEntry:
    _check_name(name)
    if name not in entries:
        raise ThirdPartySkillError(f"No third-party skill named {name!r} is installed.")
    return entries[name]


# --- Operations (used by admin_cli.py) ---------------------------------------


def install(
    source: str,
    skill_path: str,
    *,
    ref: str | None,
    install_dir: str | Path,
    lock_path: str | Path,
    reserved_names: Iterable[str] = (),
) -> InstallResult:
    """Fetch one upstream skill directory, record it, leave it **disabled**."""
    install_dir = Path(install_dir)
    clone_url, web_base = parse_source(source)
    skill_path = validate_skill_path(skill_path)
    entries = read_lock(lock_path)
    with _staging_root(install_dir) as tmp:
        staged = _fetch_and_stage(clone_url, web_base, skill_path, ref, install_dir, Path(tmp))
        name = staged.name
        if name in set(reserved_names):
            raise ThirdPartySkillError(
                f"Skill name {name!r} collides with a first-party skill in the shared catalog."
            )
        if name in entries:
            raise ThirdPartySkillError(
                f"{name!r} is already installed; use `update` or `remove` first."
            )
        dest = install_dir / name
        if dest.exists():
            raise ThirdPartySkillError(
                f"{dest} exists but is not in the lockfile; remove it manually first."
            )
        _place(staged.dir, dest)
    entries[name] = staged.entry
    write_lock(lock_path, entries)
    return InstallResult(name=name, entry=staged.entry, warnings=staged.warnings)


def update(
    name: str,
    *,
    ref: str | None,
    install_dir: str | Path,
    lock_path: str | Path,
) -> tuple[LockEntry, bool]:
    """Re-fetch at `ref` (default: the originally requested ref).

    Returns `(entry, reapproval_required)`. Any change in content or license
    disables the skill and clears a prior unknown-license acknowledgement:
    new upstream content is new untrusted input and needs a fresh decision.
    """
    install_dir = Path(install_dir)
    entries = read_lock(lock_path)
    old = _require(entries, name)
    clone_url, web_base = parse_source(old.source)
    effective_ref = ref if ref is not None else old.requested_ref
    with _staging_root(install_dir) as tmp:
        staged = _fetch_and_stage(
            clone_url, web_base, old.path, effective_ref, install_dir, Path(tmp)
        )
        if staged.name != name:
            raise ThirdPartySkillError(
                f"Upstream skill at {old.path!r} is now named {staged.name!r}, not {name!r}; "
                "remove and reinstall it instead."
            )
        _place(staged.dir, install_dir / name)
    new = staged.entry
    changed = new.integrity != old.integrity or new.license != old.license
    new.enabled = old.enabled and not changed
    new.license_acknowledged = old.license_acknowledged and not changed
    entries[name] = new
    write_lock(lock_path, entries)
    return new, changed and old.enabled


def remove(name: str, *, install_dir: str | Path, lock_path: str | Path) -> None:
    """Delete an installed skill's files and its lockfile entry."""
    entries = read_lock(lock_path)
    _require(entries, name)
    dest = Path(install_dir) / name
    if dest.exists():
        shutil.rmtree(dest)
    del entries[name]
    write_lock(lock_path, entries)


def set_enabled(
    name: str,
    enabled: bool,
    *,
    install_dir: str | Path,
    lock_path: str | Path,
    accept_unknown_license: bool = False,
) -> LockEntry:
    """Enable/disable one skill. Enabling verifies integrity and license state."""
    entries = read_lock(lock_path)
    entry = _require(entries, name)
    if enabled:
        status = integrity_status(Path(install_dir) / name, entry)
        if status != "ok":
            raise ThirdPartySkillError(
                f"{name!r} is {status} on disk (does not match the lockfile); run `sync` first."
            )
        if not entry.license_established and not (
            accept_unknown_license or entry.license_acknowledged
        ):
            reasons = "; ".join(entry.license_notes) or "no recognizable license"
            raise ThirdPartySkillError(
                f"The license for {name!r} could not be established ({reasons}). Review the "
                f"upstream terms at {entry.source_url}, then re-run with "
                "--accept-unknown-license if you decide to use it anyway."
            )
        if not entry.license_established:
            entry.license_acknowledged = True
    entry.enabled = enabled
    write_lock(lock_path, entries)
    return entry


def list_installed(
    *, install_dir: str | Path, lock_path: str | Path
) -> list[tuple[str, LockEntry, str]]:
    """`(name, entry, integrity_status)` for every lockfile entry."""
    install_dir = Path(install_dir)
    return [
        (name, entry, integrity_status(install_dir / name, entry))
        for name, entry in sorted(read_lock(lock_path).items())
    ]


def sync(*, install_dir: str | Path, lock_path: str | Path) -> list[tuple[str, str]]:
    """Restore every locked skill at its pinned revision; verify its hash.

    A fetched copy whose hash differs from the lockfile is **not** installed
    (status `integrity-mismatch`). Enablement is never changed here.
    """
    install_dir = Path(install_dir)
    results: list[tuple[str, str]] = []
    for name, entry in sorted(read_lock(lock_path).items()):
        if integrity_status(install_dir / name, entry) == "ok":
            results.append((name, "ok"))
            continue
        clone_url, web_base = parse_source(entry.source)
        try:
            with _staging_root(install_dir) as tmp:
                staged = _fetch_and_stage(
                    clone_url, web_base, entry.path, entry.revision, install_dir, Path(tmp)
                )
                if staged.name != name or staged.entry.integrity != entry.integrity:
                    results.append((name, "integrity-mismatch"))
                    continue
                _place(staged.dir, install_dir / name)
            results.append((name, "restored"))
        except ThirdPartySkillError as exc:
            results.append((name, f"error: {exc}"))
    return results


# --- Loader (used by agent.py) -----------------------------------------------


def load_enabled_skills(
    install_dir: str | Path,
    lock_path: str | Path,
    reserved_names: Iterable[str] = (),
) -> list[Skill]:
    """Load only enabled, license-cleared, hash-verified third-party skills.

    Re-checks every condition the CLI enforces, since the lockfile is a
    plain file anyone can edit. Directories on disk with no lockfile entry
    are ignored — unlike the SDK's own installer, nothing is auto-discovered.
    """
    install_dir = Path(install_dir)
    reserved = set(reserved_names)
    skills: list[Skill] = []
    for name, entry in sorted(read_lock(lock_path).items()):
        if not entry.enabled:
            continue
        if not entry.license_established and not entry.license_acknowledged:
            logger.warning("Skipping third-party skill %r: license not acknowledged", name)
            continue
        if name in reserved:
            logger.warning(
                "Skipping third-party skill %r: name collides with a first-party skill", name
            )
            continue
        skill_dir = install_dir / name
        status = integrity_status(skill_dir, entry)
        if status != "ok":
            logger.warning("Skipping third-party skill %r: files %s (run sync)", name, status)
            continue
        try:
            skill = Skill.load(find_skill_md(skill_dir), strict=True, skip_mcp=True)
        except _SKILL_LOAD_ERRORS as exc:
            logger.warning("Skipping third-party skill %r: %s", name, exc)
            continue
        if skill.name != name:
            logger.warning("Skipping third-party skill %r: SKILL.md names %r", name, skill.name)
            continue
        skills.append(
            skill.model_copy(update={"content": neutralize_inline_commands(skill.content)})
        )
    return skills
