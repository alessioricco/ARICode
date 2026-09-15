"""Tests for harness.config — pure, no SDK, no network.

Every test passes an explicit `env` dict so the real environment and .env file are
never touched.
"""

import pytest

from harness.config import (
    DEFAULT_CONFIRM_MODE,
    DEFAULT_DOCKER_IMAGE,
    DEFAULT_DOCKER_PLATFORM,
    DEFAULT_EXECUTION,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_VERIFY_RETRIES,
    DEFAULT_PROJECTS_DIR,
    DEFAULT_SKILLS_DIR,
    DEFAULT_VERIFY_TESTS,
    DEFAULT_WORKSPACE,
    Config,
    ConfigError,
    load_config,
    override_llm,
)


def _base_env(**overrides: str) -> dict[str, str]:
    env = {"LLM_MODEL": "anthropic/claude-sonnet-4-5-20250929", "LLM_API_KEY": "sk-test"}
    env.update(overrides)
    return env


def test_minimal_valid_env_applies_defaults():
    cfg = load_config(_base_env())
    assert isinstance(cfg, Config)
    assert cfg.model == "anthropic/claude-sonnet-4-5-20250929"
    assert cfg.api_key == "sk-test"
    assert cfg.base_url is None
    assert cfg.workspace == DEFAULT_WORKSPACE
    assert cfg.max_iterations == DEFAULT_MAX_ITERATIONS
    assert cfg.confirm_mode == DEFAULT_CONFIRM_MODE
    assert cfg.execution == DEFAULT_EXECUTION
    assert cfg.projects_dir == DEFAULT_PROJECTS_DIR
    assert cfg.skills_dir == DEFAULT_SKILLS_DIR
    assert cfg.docker_image == DEFAULT_DOCKER_IMAGE
    assert cfg.docker_platform == DEFAULT_DOCKER_PLATFORM
    assert cfg.verify_tests == DEFAULT_VERIFY_TESTS
    assert cfg.max_verify_retries == DEFAULT_MAX_VERIFY_RETRIES


def test_all_values_parsed():
    cfg = load_config(
        _base_env(
            LLM_BASE_URL="http://localhost:11434",
            HARNESS_WORKSPACE="/tmp/ws",
            HARNESS_MAX_ITERATIONS="12",
            HARNESS_CONFIRM_MODE="always",
            HARNESS_EXECUTION="docker",
            HARNESS_PROJECTS_DIR="/tmp/projects",
            HARNESS_SKILLS_DIR="/tmp/skills",
            HARNESS_DOCKER_IMAGE="myorg/agent-server:custom",
            HARNESS_DOCKER_PLATFORM="linux/amd64",
            HARNESS_VERIFY_TESTS="never",
            HARNESS_MAX_VERIFY_RETRIES="5",
        )
    )
    assert cfg.base_url == "http://localhost:11434"
    assert cfg.workspace == "/tmp/ws"
    assert cfg.max_iterations == 12
    assert cfg.confirm_mode == "always"
    assert cfg.execution == "docker"
    assert cfg.projects_dir == "/tmp/projects"
    assert cfg.skills_dir == "/tmp/skills"
    assert cfg.docker_image == "myorg/agent-server:custom"
    assert cfg.docker_platform == "linux/amd64"
    assert cfg.verify_tests == "never"
    assert cfg.max_verify_retries == 5


def test_invalid_verify_tests_raises():
    with pytest.raises(ConfigError, match="HARNESS_VERIFY_TESTS"):
        load_config(_base_env(HARNESS_VERIFY_TESTS="sometimes"))


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_invalid_max_verify_retries_raises(bad):
    with pytest.raises(ConfigError, match="HARNESS_MAX_VERIFY_RETRIES"):
        load_config(_base_env(HARNESS_MAX_VERIFY_RETRIES=bad))


def test_invalid_docker_platform_raises():
    with pytest.raises(ConfigError, match="HARNESS_DOCKER_PLATFORM"):
        load_config(_base_env(HARNESS_DOCKER_PLATFORM="linux/mips"))


def test_missing_model_raises():
    with pytest.raises(ConfigError, match="LLM_MODEL is required"):
        load_config({"LLM_API_KEY": "sk-test"})


def test_missing_key_without_base_url_raises():
    with pytest.raises(ConfigError, match="LLM_API_KEY is required"):
        load_config({"LLM_MODEL": "anthropic/claude-sonnet-4-5-20250929"})


def test_base_url_allows_missing_key():
    # Local models can be keyless: base_url present, api_key absent -> OK.
    cfg = load_config(
        {"LLM_MODEL": "ollama/llama3", "LLM_BASE_URL": "http://localhost:11434"}
    )
    assert cfg.api_key is None
    assert cfg.base_url == "http://localhost:11434"


def test_blank_values_treated_as_absent():
    cfg = load_config(_base_env(HARNESS_WORKSPACE="   ", HARNESS_CONFIRM_MODE=""))
    assert cfg.workspace == DEFAULT_WORKSPACE
    assert cfg.confirm_mode == DEFAULT_CONFIRM_MODE


def test_whitespace_is_trimmed():
    cfg = load_config(_base_env(LLM_MODEL="  openai/gpt-4o  "))
    assert cfg.model == "openai/gpt-4o"


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "3.5"])
def test_invalid_max_iterations_raises(bad):
    with pytest.raises(ConfigError, match="HARNESS_MAX_ITERATIONS"):
        load_config(_base_env(HARNESS_MAX_ITERATIONS=bad))


def test_invalid_confirm_mode_raises():
    with pytest.raises(ConfigError, match="HARNESS_CONFIRM_MODE"):
        load_config(_base_env(HARNESS_CONFIRM_MODE="sometimes"))


def test_invalid_execution_raises():
    with pytest.raises(ConfigError, match="HARNESS_EXECUTION"):
        load_config(_base_env(HARNESS_EXECUTION="kubernetes"))


def test_choices_are_case_insensitive():
    cfg = load_config(_base_env(HARNESS_CONFIRM_MODE="ALWAYS", HARNESS_EXECUTION="Docker"))
    assert cfg.confirm_mode == "always"
    assert cfg.execution == "docker"


def test_config_is_frozen():
    cfg = load_config(_base_env())
    with pytest.raises(Exception):
        cfg.model = "openai/gpt-4o"  # type: ignore[misc]


def test_override_llm_applies_only_given_fields():
    cfg = load_config(_base_env())

    overridden = override_llm(cfg, model="openai/gpt-4o")

    assert overridden.model == "openai/gpt-4o"
    assert overridden.api_key == cfg.api_key
    assert overridden.base_url == cfg.base_url


def test_override_llm_applies_all_fields():
    cfg = load_config(_base_env())

    overridden = override_llm(
        cfg, model="ollama/llama3", api_key=None, base_url="http://localhost:11434"
    )

    assert overridden.model == "ollama/llama3"
    assert overridden.base_url == "http://localhost:11434"
    # api_key=None means "not overridden", not "cleared" -> unchanged.
    assert overridden.api_key == cfg.api_key


def test_override_llm_no_args_returns_same_config():
    cfg = load_config(_base_env())

    assert override_llm(cfg) == cfg


def test_override_llm_blank_model_raises():
    cfg = load_config(_base_env())

    with pytest.raises(ConfigError, match="Model override must not be blank"):
        override_llm(cfg, model="   ")


def test_override_llm_does_not_mutate_original():
    cfg = load_config(_base_env())

    override_llm(cfg, model="openai/gpt-4o")

    assert cfg.model == "anthropic/claude-sonnet-4-5-20250929"
