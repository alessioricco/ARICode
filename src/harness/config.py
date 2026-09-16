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

CONFIRM_MODES = ("never", "always")
EXECUTION_MODES = ("local", "docker")
DOCKER_PLATFORMS = ("linux/amd64", "linux/arm64")
VERIFY_TESTS_MODES = ("always", "never")

DEFAULT_WORKSPACE = "."
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_CONFIRM_MODE = "never"
DEFAULT_EXECUTION = "local"
DEFAULT_PROJECTS_DIR = "./projects"
DEFAULT_SKILLS_DIR = "./skills"
DEFAULT_VERIFY_TESTS = "always"
DEFAULT_MAX_VERIFY_RETRIES = 2
DEFAULT_DOCKER_IMAGE = "coding-agent-harness/agent-server:local"
DEFAULT_DOCKER_PLATFORM = (
    "linux/arm64" if _platform.machine().lower() in ("arm64", "aarch64") else "linux/amd64"
)


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

    # Provider-neutral reasoning effort, consumed by llm.py and passed
    # straight through to the SDK's own `LLM.reasoning_effort` (LiteLLM
    # translates it per-provider — see ROADMAP.md's decisions log).
    # Deliberately not validated against a fixed choice list: the SDK's own
    # field accepts forward-compatible values beyond its documented ones
    # ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', ...), so
    # this harness shouldn't reject one it doesn't yet know about. None
    # means "don't override" — the SDK's own default ('high') applies.
    reasoning_effort: str | None = None


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
        reasoning_effort=reasoning_effort,
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
