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
from dataclasses import dataclass

CONFIRM_MODES = ("never", "always")
EXECUTION_MODES = ("local", "docker")
DOCKER_PLATFORMS = ("linux/amd64", "linux/arm64")

DEFAULT_WORKSPACE = "."
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_CONFIRM_MODE = "never"
DEFAULT_EXECUTION = "local"
DEFAULT_PROJECTS_DIR = "./projects"
DEFAULT_SKILLS_DIR = "./skills"
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
    )
