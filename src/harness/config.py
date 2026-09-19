"""Configuration for the coding-agent harness.

Pure environment parsing — deliberately free of any OpenHands SDK import so it can
be unit-tested with no network and no SDK installed. `llm.py` consumes the LLM_*
values (wrapping the key in SecretStr when it builds the SDK's LLM); everything
else is harness policy.

The model-agnostic invariant lives here: the provider/model is entirely a function
of the LLM_MODEL env var. Nothing in this module (or anywhere in src/) hardcodes a
provider, model, or base URL.
"""

from __future__ import annotations

import os
import platform as _platform
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

CONFIRM_MODES = ("never", "always")
EXECUTION_MODES = ("local", "docker")
DOCKER_PLATFORMS = ("linux/amd64", "linux/arm64")
VERIFY_TESTS_MODES = ("always", "never")
TASK_STORE_BACKENDS = ("memory", "redis", "sqlite", "mysql", "postgres")
MODEL_SELECTION_MODES = ("manual", "auto")

DEFAULT_WORKSPACE = "."
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_CONFIRM_MODE = "never"
DEFAULT_EXECUTION = "local"
DEFAULT_PROJECTS_DIR = "./projects"
DEFAULT_SKILLS_DIR = "./skills"
DEFAULT_VERIFY_TESTS = "always"
DEFAULT_MAX_VERIFY_RETRIES = 2
DEFAULT_MAX_TASK_SECONDS = 1800
DEFAULT_DOCKER_IMAGE = "coding-agent-harness/agent-server:local"
DEFAULT_DOCKER_PLATFORM = (
    "linux/arm64" if _platform.machine().lower() in ("arm64", "aarch64") else "linux/amd64"
)

# --- Server-mode task registry persistence (see task_store.py) -------------
DEFAULT_TASK_STORE = "memory"
# 0 means "keep forever" — matches the pre-existing (unbounded, in-memory)
# behavior exactly, so enabling a store backend alone never changes
# retention; a TTL is something a caller opts into separately.
DEFAULT_TASK_TTL_SECONDS = 0
DEFAULT_TASK_STORE_SQLITE_PATH = "./harness_tasks.db"
DEFAULT_TASK_STORE_MYSQL_HOST = "localhost"
DEFAULT_TASK_STORE_MYSQL_PORT = 3306
DEFAULT_TASK_STORE_MYSQL_USER = "root"
DEFAULT_TASK_STORE_MYSQL_DATABASE = "harness"
DEFAULT_TASK_STORE_POSTGRES_HOST = "localhost"
DEFAULT_TASK_STORE_POSTGRES_PORT = 5432
DEFAULT_TASK_STORE_POSTGRES_USER = "postgres"
DEFAULT_TASK_STORE_POSTGRES_DATABASE = "harness"
DEFAULT_TASK_STORE_REDIS_HOST = "localhost"
DEFAULT_TASK_STORE_REDIS_PORT = 6379
DEFAULT_TASK_STORE_REDIS_DB = 0

# --- Deterministic auto model selection (see model_catalog.py) -------------
DEFAULT_MODEL_SELECTION = "manual"  # one of MODEL_SELECTION_MODES
DEFAULT_MODELS_FILE = "./models.yaml"


class ConfigError(ValueError):
    """Raised when the environment is missing or invalid. Message is user-facing."""


@dataclass(frozen=True)
class Config:
    """Resolved, validated harness configuration."""

    # LLM selection (consumed by llm.py). Provider is encoded in `model`'s prefix.
    model: str
    api_key: str | None
    base_url: str | None

    # Harness policy (not the LLM's concern).
    workspace: str
    max_iterations: int
    confirm_mode: str  # one of CONFIRM_MODES
    execution: str  # one of EXECUTION_MODES

    # Where generated projects live: workspace defaults to plain HARNESS_WORKSPACE,
    # but `--project NAME` (cli.py) overrides it to `projects_dir/NAME`, created if
    # missing, so each project's generated software lands in its own subfolder.
    projects_dir: str = DEFAULT_PROJECTS_DIR

    # Shared skill catalog loaded into every agent's AgentContext (see skills.py).
    # Trigger-based (keyword/task/path), not project-specific — see MANUAL.md.
    skills_dir: str = DEFAULT_SKILLS_DIR

    # Only consulted when execution == "docker" (see workspace.py).
    docker_image: str = DEFAULT_DOCKER_IMAGE
    docker_platform: str = DEFAULT_DOCKER_PLATFORM  # one of DOCKER_PLATFORMS

    # Post-hoc test verification (see runner.py's _verify_tests_and_retry):
    # after the agent finishes, the harness itself re-runs the project's
    # pytest suite (no LLM involved) and, if it fails, sends the real
    # failure output back and lets the agent try again, up to this many
    # times — a safety net for trusting the agent's own "it's done" self-
    # report, which live testing showed can be wrong (see ROADMAP.md).
    verify_tests: str = DEFAULT_VERIFY_TESTS  # one of VERIFY_TESTS_MODES
    max_verify_retries: int = DEFAULT_MAX_VERIFY_RETRIES

    # A shared, task-level wall-clock budget spanning every phase of one task
    # — the initial conversation.run(), and all of _enforce_task_tracker_
    # completion's and _verify_and_report's retries — not just the SDK's own
    # max_iteration_per_run, which resets on every single run() call and so
    # cannot on its own bound how long one task can run in total (see
    # runner.py's _run_with_confirmation and ROADMAP.md's decisions log).
    max_task_seconds: int = DEFAULT_MAX_TASK_SECONDS

    # Provider-neutral reasoning effort, consumed by llm.py and passed
    # straight through to the SDK's own `LLM.reasoning_effort` (LiteLLM
    # translates it per-provider — see ROADMAP.md's decisions log).
    # Deliberately not validated against a fixed choice list: the SDK's own
    # field accepts forward-compatible values beyond its documented ones
    # ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', ...), so
    # this harness shouldn't reject one it doesn't yet know about. None
    # means "don't override" — the SDK's own default ('high') applies.
    reasoning_effort: str | None = None

    # Server-mode task registry persistence (see task_store.py). Default
    # ("memory", no TTL) is byte-for-byte the pre-existing behavior — this
    # entire feature is additive, never a silent behavior change for an
    # existing server-mode deployment.
    task_store: str = DEFAULT_TASK_STORE  # one of TASK_STORE_BACKENDS
    task_ttl_seconds: int = DEFAULT_TASK_TTL_SECONDS  # 0 = keep forever

    task_store_sqlite_path: str = DEFAULT_TASK_STORE_SQLITE_PATH

    task_store_mysql_host: str = DEFAULT_TASK_STORE_MYSQL_HOST
    task_store_mysql_port: int = DEFAULT_TASK_STORE_MYSQL_PORT
    task_store_mysql_user: str = DEFAULT_TASK_STORE_MYSQL_USER
    task_store_mysql_password: str | None = None
    task_store_mysql_database: str = DEFAULT_TASK_STORE_MYSQL_DATABASE

    task_store_postgres_host: str = DEFAULT_TASK_STORE_POSTGRES_HOST
    task_store_postgres_port: int = DEFAULT_TASK_STORE_POSTGRES_PORT
    task_store_postgres_user: str = DEFAULT_TASK_STORE_POSTGRES_USER
    task_store_postgres_password: str | None = None
    task_store_postgres_database: str = DEFAULT_TASK_STORE_POSTGRES_DATABASE

    task_store_redis_host: str = DEFAULT_TASK_STORE_REDIS_HOST
    task_store_redis_port: int = DEFAULT_TASK_STORE_REDIS_PORT
    task_store_redis_db: int = DEFAULT_TASK_STORE_REDIS_DB
    task_store_redis_password: str | None = None
    task_store_redis_use_tls: bool = False

    # Opt-in interactive mode (see runner.py's stream_task interactive loop
    # and agent.py's build_agent): when true, drops _AUTONOMOUS_SUFFIX from
    # the system prompt so the agent may pause/ask instead of being told to
    # always push forward on its own. Only meaningful for a caller that also
    # supplies an on_awaiting_input callback (cli.py's --interactive does;
    # server.py never does, same as HARNESS_CONFIRM_MODE=always having no
    # effect there) — default false, current fully-autonomous behavior
    # unchanged.
    interactive: bool = False

    # Deterministic auto model selection (see model_catalog.py/
    # model_selection.py). Default ("manual") is byte-for-byte the
    # pre-existing behavior — cfg.model is used exactly as it always has
    # been; models_file is only ever read when model_selection == "auto".
    model_selection: str = DEFAULT_MODEL_SELECTION  # one of MODEL_SELECTION_MODES
    models_file: str = DEFAULT_MODELS_FILE


def _clean(value: str | None) -> str | None:
    """Trim whitespace; treat empty string as absent."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_positive_int(value: str | None, *, default: int, name: str) -> int:
    if _clean(value) is None:
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer, got {value!r}.") from None
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive integer, got {parsed}.")
    return parsed


def _parse_choice(value: str | None, *, default: str, choices: tuple[str, ...], name: str) -> str:
    resolved = (_clean(value) or default).lower()
    if resolved not in choices:
        allowed = " | ".join(choices)
        raise ConfigError(f"{name} must be one of: {allowed}. Got {resolved!r}.")
    return resolved


def _parse_nonnegative_int(value: str | None, *, default: int, name: str) -> int:
    """Like `_parse_positive_int` but allows `0` — used only where `0` is a
    real, meaningful setting ("no limit"/"keep forever"), not "unset".
    """
    if _clean(value) is None:
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer, got {value!r}.") from None
    if parsed < 0:
        raise ConfigError(f"{name} must be zero or a positive integer, got {parsed}.")
    return parsed


def _parse_bool(value: str | None, *, default: bool, name: str) -> bool:
    cleaned = _clean(value)
    if cleaned is None:
        return default
    lowered = cleaned.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {value!r}.")


def load_config(env: Mapping[str, str] | None = None, *, dotenv_path: str = ".env") -> Config:
    """Load and validate configuration.

    Args:
        env: Optional mapping to read from (defaults to os.environ). Passing a dict
            makes this fully testable without touching the real environment or a
            .env file.
        dotenv_path: Path to a .env file loaded into os.environ when `env` is None.
            Missing file is ignored.

    Raises:
        ConfigError: if required values are missing or any value is invalid.
    """
    if env is None:
        # Lazy import so tests that pass `env` never require python-dotenv.
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path)
        except ImportError:  # pragma: no cover - dotenv is a declared dependency
            pass
        env = os.environ

    model = _clean(env.get("LLM_MODEL"))
    if model is None:
        raise ConfigError(
            "LLM_MODEL is required (e.g. 'anthropic/claude-sonnet-4-5-20250929'). "
            "Set it in .env. Switching provider = changing only this value."
        )

    api_key = _clean(env.get("LLM_API_KEY"))
    base_url = _clean(env.get("LLM_BASE_URL"))
    if api_key is None and base_url is None:
        raise ConfigError(
            "LLM_API_KEY is required unless LLM_BASE_URL is set (e.g. a local "
            "Ollama/vLLM/LM Studio endpoint that needs no key)."
        )
    reasoning_effort = _clean(env.get("LLM_REASONING_EFFORT"))

    workspace = _clean(env.get("HARNESS_WORKSPACE")) or DEFAULT_WORKSPACE
    max_iterations = _parse_positive_int(
        env.get("HARNESS_MAX_ITERATIONS"),
        default=DEFAULT_MAX_ITERATIONS,
        name="HARNESS_MAX_ITERATIONS",
    )
    confirm_mode = _parse_choice(
        env.get("HARNESS_CONFIRM_MODE"),
        default=DEFAULT_CONFIRM_MODE,
        choices=CONFIRM_MODES,
        name="HARNESS_CONFIRM_MODE",
    )
    execution = _parse_choice(
        env.get("HARNESS_EXECUTION"),
        default=DEFAULT_EXECUTION,
        choices=EXECUTION_MODES,
        name="HARNESS_EXECUTION",
    )
    projects_dir = _clean(env.get("HARNESS_PROJECTS_DIR")) or DEFAULT_PROJECTS_DIR
    skills_dir = _clean(env.get("HARNESS_SKILLS_DIR")) or DEFAULT_SKILLS_DIR
    docker_image = _clean(env.get("HARNESS_DOCKER_IMAGE")) or DEFAULT_DOCKER_IMAGE
    docker_platform = _parse_choice(
        env.get("HARNESS_DOCKER_PLATFORM"),
        default=DEFAULT_DOCKER_PLATFORM,
        choices=DOCKER_PLATFORMS,
        name="HARNESS_DOCKER_PLATFORM",
    )
    verify_tests = _parse_choice(
        env.get("HARNESS_VERIFY_TESTS"),
        default=DEFAULT_VERIFY_TESTS,
        choices=VERIFY_TESTS_MODES,
        name="HARNESS_VERIFY_TESTS",
    )
    max_verify_retries = _parse_positive_int(
        env.get("HARNESS_MAX_VERIFY_RETRIES"),
        default=DEFAULT_MAX_VERIFY_RETRIES,
        name="HARNESS_MAX_VERIFY_RETRIES",
    )
    max_task_seconds = _parse_positive_int(
        env.get("HARNESS_MAX_TASK_SECONDS"),
        default=DEFAULT_MAX_TASK_SECONDS,
        name="HARNESS_MAX_TASK_SECONDS",
    )

    task_store = _parse_choice(
        env.get("HARNESS_TASK_STORE"),
        default=DEFAULT_TASK_STORE,
        choices=TASK_STORE_BACKENDS,
        name="HARNESS_TASK_STORE",
    )
    task_ttl_seconds = _parse_nonnegative_int(
        env.get("HARNESS_TASK_TTL_SECONDS"),
        default=DEFAULT_TASK_TTL_SECONDS,
        name="HARNESS_TASK_TTL_SECONDS",
    )
    task_store_sqlite_path = (
        _clean(env.get("HARNESS_TASK_STORE_SQLITE_PATH")) or DEFAULT_TASK_STORE_SQLITE_PATH
    )
    task_store_mysql_host = (
        _clean(env.get("HARNESS_TASK_STORE_MYSQL_HOST")) or DEFAULT_TASK_STORE_MYSQL_HOST
    )
    task_store_mysql_port = _parse_positive_int(
        env.get("HARNESS_TASK_STORE_MYSQL_PORT"),
        default=DEFAULT_TASK_STORE_MYSQL_PORT,
        name="HARNESS_TASK_STORE_MYSQL_PORT",
    )
    task_store_mysql_user = (
        _clean(env.get("HARNESS_TASK_STORE_MYSQL_USER")) or DEFAULT_TASK_STORE_MYSQL_USER
    )
    task_store_mysql_password = _clean(env.get("HARNESS_TASK_STORE_MYSQL_PASSWORD"))
    task_store_mysql_database = (
        _clean(env.get("HARNESS_TASK_STORE_MYSQL_DATABASE")) or DEFAULT_TASK_STORE_MYSQL_DATABASE
    )
    task_store_postgres_host = (
        _clean(env.get("HARNESS_TASK_STORE_POSTGRES_HOST")) or DEFAULT_TASK_STORE_POSTGRES_HOST
    )
    task_store_postgres_port = _parse_positive_int(
        env.get("HARNESS_TASK_STORE_POSTGRES_PORT"),
        default=DEFAULT_TASK_STORE_POSTGRES_PORT,
        name="HARNESS_TASK_STORE_POSTGRES_PORT",
    )
    task_store_postgres_user = (
        _clean(env.get("HARNESS_TASK_STORE_POSTGRES_USER")) or DEFAULT_TASK_STORE_POSTGRES_USER
    )
    task_store_postgres_password = _clean(env.get("HARNESS_TASK_STORE_POSTGRES_PASSWORD"))
    task_store_postgres_database = (
        _clean(env.get("HARNESS_TASK_STORE_POSTGRES_DATABASE"))
        or DEFAULT_TASK_STORE_POSTGRES_DATABASE
    )
    task_store_redis_host = (
        _clean(env.get("HARNESS_TASK_STORE_REDIS_HOST")) or DEFAULT_TASK_STORE_REDIS_HOST
    )
    task_store_redis_port = _parse_positive_int(
        env.get("HARNESS_TASK_STORE_REDIS_PORT"),
        default=DEFAULT_TASK_STORE_REDIS_PORT,
        name="HARNESS_TASK_STORE_REDIS_PORT",
    )
    task_store_redis_db = _parse_nonnegative_int(
        env.get("HARNESS_TASK_STORE_REDIS_DB"),
        default=DEFAULT_TASK_STORE_REDIS_DB,
        name="HARNESS_TASK_STORE_REDIS_DB",
    )
    task_store_redis_password = _clean(env.get("HARNESS_TASK_STORE_REDIS_PASSWORD"))
    task_store_redis_use_tls = _parse_bool(
        env.get("HARNESS_TASK_STORE_REDIS_USE_TLS"),
        default=False,
        name="HARNESS_TASK_STORE_REDIS_USE_TLS",
    )
    interactive = _parse_bool(
        env.get("HARNESS_INTERACTIVE"),
        default=False,
        name="HARNESS_INTERACTIVE",
    )
    model_selection = _parse_choice(
        env.get("HARNESS_MODEL_SELECTION"),
        default=DEFAULT_MODEL_SELECTION,
        choices=MODEL_SELECTION_MODES,
        name="HARNESS_MODEL_SELECTION",
    )
    models_file = _clean(env.get("HARNESS_MODELS_FILE")) or DEFAULT_MODELS_FILE

    return Config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        workspace=workspace,
        max_iterations=max_iterations,
        confirm_mode=confirm_mode,
        execution=execution,
        projects_dir=projects_dir,
        skills_dir=skills_dir,
        docker_image=docker_image,
        docker_platform=docker_platform,
        verify_tests=verify_tests,
        max_verify_retries=max_verify_retries,
        max_task_seconds=max_task_seconds,
        reasoning_effort=reasoning_effort,
        task_store=task_store,
        task_ttl_seconds=task_ttl_seconds,
        task_store_sqlite_path=task_store_sqlite_path,
        task_store_mysql_host=task_store_mysql_host,
        task_store_mysql_port=task_store_mysql_port,
        task_store_mysql_user=task_store_mysql_user,
        task_store_mysql_password=task_store_mysql_password,
        task_store_mysql_database=task_store_mysql_database,
        task_store_postgres_host=task_store_postgres_host,
        task_store_postgres_port=task_store_postgres_port,
        task_store_postgres_user=task_store_postgres_user,
        task_store_postgres_password=task_store_postgres_password,
        task_store_postgres_database=task_store_postgres_database,
        task_store_redis_host=task_store_redis_host,
        task_store_redis_port=task_store_redis_port,
        task_store_redis_db=task_store_redis_db,
        task_store_redis_password=task_store_redis_password,
        task_store_redis_use_tls=task_store_redis_use_tls,
        interactive=interactive,
        model_selection=model_selection,
        models_file=models_file,
    )


def override_llm(
    cfg: Config,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
) -> Config:
    """Return a copy of `cfg` with LLM_MODEL/LLM_API_KEY/LLM_BASE_URL/
    LLM_REASONING_EFFORT overridden.

    Lets a single call (CLI flag or a server request field) swap provider/model
    (or just the reasoning effort, independent of provider) for one run without
    touching `.env` — e.g. to compare how two models, or two effort levels of
    the same model, handle the same task. Only arguments that are not None are
    applied; anything else keeps `cfg`'s existing value. Does not re-validate
    the api_key-or-base_url-required rule from `load_config` — `cfg` already
    satisfied it, and an override is additive, not a fresh load.
    """
    updates: dict[str, str | None] = {}
    if model is not None:
        cleaned_model = _clean(model)
        if cleaned_model is None:
            raise ConfigError("Model override must not be blank.")
        updates["model"] = cleaned_model
    if api_key is not None:
        updates["api_key"] = _clean(api_key)
    if base_url is not None:
        updates["base_url"] = _clean(base_url)
    if reasoning_effort is not None:
        updates["reasoning_effort"] = _clean(reasoning_effort)
    return replace(cfg, **updates) if updates else cfg


def resolve_project_dir(projects_dir: str, project: str) -> str:
    """Resolve a caller-supplied `project` name to a path guaranteed to stay
    inside `projects_dir` — the one shared resolver `cli.py`'s `--project`
    and `server.py`'s `project` request field both go through, instead of
    each doing its own unguarded `os.path.join(projects_dir, project)`.

    That unguarded join was a real bug, not just missing hardening:
    `os.path.join(a, b)` silently discards `a` when `b` is absolute, so an
    absolute `project` value (e.g. `--project /etc/cron.d`, or the same
    value in an unauthenticated `POST /tasks` request) redirected the
    agent's entire workspace — including its terminal and file-editor tools
    — to an arbitrary path on the host. `..` traversal had the same effect
    more subtly.

    Checked in order: `project` itself must be non-empty, relative, and
    contain no `..` segment (rejected before any path is ever built, so the
    error names the exact problem rather than a generic "path escaped").
    The resolved path is then checked against `projects_dir` with symlinks
    followed on both sides (`os.path.realpath`) — not just a lexical
    comparison — so a project name that looks safe but resolves through an
    existing symlink out of `projects_dir` is caught too.

    Raises `ConfigError` — the one error type both callers already catch
    and turn into a clean CLI/HTTP error — rather than a bespoke exception.
    """
    if not project or project in (".", ".."):
        raise ConfigError(f"Invalid project name: {project!r}")
    if os.path.isabs(project):
        raise ConfigError(f"Project name must be relative, not absolute: {project!r}")
    if any(part == ".." for part in Path(project).parts):
        raise ConfigError(f"Project name must not contain '..': {project!r}")

    projects_root = os.path.abspath(projects_dir)
    project_dir = os.path.join(projects_root, project)

    resolved_root = os.path.realpath(projects_root)
    resolved_project_dir = os.path.realpath(project_dir)
    if not Path(resolved_project_dir).is_relative_to(Path(resolved_root)):
        raise ConfigError(f"Project path escapes HARNESS_PROJECTS_DIR: {project!r}")

    return project_dir
