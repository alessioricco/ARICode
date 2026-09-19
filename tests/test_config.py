"""Tests for harness.config — pure, no SDK, no network.

Every test passes an explicit `env` dict so the real environment and .env file are
never touched.
"""

import os

import pytest

from harness.config import (
    DEFAULT_CONFIRM_MODE,
    DEFAULT_DOCKER_IMAGE,
    DEFAULT_DOCKER_PLATFORM,
    DEFAULT_EXECUTION,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_TASK_SECONDS,
    DEFAULT_MAX_VERIFY_RETRIES,
    DEFAULT_PROJECTS_DIR,
    DEFAULT_SKILLS_DIR,
    DEFAULT_TASK_STORE,
    DEFAULT_TASK_STORE_MYSQL_PORT,
    DEFAULT_TASK_STORE_POSTGRES_PORT,
    DEFAULT_TASK_STORE_REDIS_PORT,
    DEFAULT_TASK_STORE_SQLITE_PATH,
    DEFAULT_TASK_TTL_SECONDS,
    DEFAULT_VERIFY_TESTS,
    DEFAULT_WORKSPACE,
    Config,
    ConfigError,
    load_config,
    override_llm,
    resolve_project_dir,
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
    assert cfg.reasoning_effort is None


def test_all_values_parsed():
    cfg = load_config(
        _base_env(
            LLM_BASE_URL="http://localhost:11434",
            LLM_REASONING_EFFORT="low",
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
    assert cfg.reasoning_effort == "low"


def test_reasoning_effort_blank_treated_as_absent():
    cfg = load_config(_base_env(LLM_REASONING_EFFORT="   "))
    assert cfg.reasoning_effort is None


def test_reasoning_effort_accepts_forward_compatible_value():
    # Deliberately not validated against a fixed choice list — see config.py's
    # comment on the field. A value the SDK/provider might add later must not
    # be rejected here.
    cfg = load_config(_base_env(LLM_REASONING_EFFORT="some-future-value"))
    assert cfg.reasoning_effort == "some-future-value"


def test_invalid_verify_tests_raises():
    with pytest.raises(ConfigError, match="HARNESS_VERIFY_TESTS"):
        load_config(_base_env(HARNESS_VERIFY_TESTS="sometimes"))


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_invalid_max_verify_retries_raises(bad):
    with pytest.raises(ConfigError, match="HARNESS_MAX_VERIFY_RETRIES"):
        load_config(_base_env(HARNESS_MAX_VERIFY_RETRIES=bad))


def test_max_task_seconds_defaults_when_unset():
    cfg = load_config(_base_env())
    assert cfg.max_task_seconds == DEFAULT_MAX_TASK_SECONDS


def test_max_task_seconds_reads_a_custom_value():
    cfg = load_config(_base_env(HARNESS_MAX_TASK_SECONDS="60"))
    assert cfg.max_task_seconds == 60


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "3.5"])
def test_invalid_max_task_seconds_raises(bad):
    with pytest.raises(ConfigError, match="HARNESS_MAX_TASK_SECONDS"):
        load_config(_base_env(HARNESS_MAX_TASK_SECONDS=bad))


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
    cfg = load_config({"LLM_MODEL": "ollama/llama3", "LLM_BASE_URL": "http://localhost:11434"})
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
        cfg,
        model="ollama/llama3",
        api_key=None,
        base_url="http://localhost:11434",
        reasoning_effort="low",
    )

    assert overridden.model == "ollama/llama3"
    assert overridden.base_url == "http://localhost:11434"
    assert overridden.reasoning_effort == "low"
    # api_key=None means "not overridden", not "cleared" -> unchanged.
    assert overridden.api_key == cfg.api_key


def test_override_llm_reasoning_effort_only():
    cfg = load_config(_base_env())

    overridden = override_llm(cfg, reasoning_effort="xhigh")

    assert overridden.reasoning_effort == "xhigh"
    assert overridden.model == cfg.model
    assert overridden.api_key == cfg.api_key
    assert overridden.base_url == cfg.base_url


def test_override_llm_blank_reasoning_effort_clears_it():
    # Unlike model (which raises on blank), a blank reasoning_effort override
    # clears it back to "unset" -> the SDK's own default applies. Matches
    # api_key/base_url's leniency, not model's strictness.
    cfg = load_config(_base_env(LLM_REASONING_EFFORT="low"))

    overridden = override_llm(cfg, reasoning_effort="   ")

    assert overridden.reasoning_effort is None


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


# --- resolve_project_dir: path containment and symlink protection ----------


def test_resolve_project_dir_returns_a_path_inside_projects_dir(tmp_path):
    projects_dir = str(tmp_path / "projects")

    resolved = resolve_project_dir(projects_dir, "myapp")

    assert resolved == os.path.join(os.path.abspath(projects_dir), "myapp")


def test_resolve_project_dir_allows_a_safe_nested_name(tmp_path):
    projects_dir = str(tmp_path / "projects")

    resolved = resolve_project_dir(projects_dir, "team/myapp")

    assert resolved == os.path.join(os.path.abspath(projects_dir), "team", "myapp")


def test_resolve_project_dir_rejects_empty_or_dot_names(tmp_path):
    projects_dir = str(tmp_path / "projects")

    for bad in ("", ".", ".."):
        with pytest.raises(ConfigError, match="Invalid project name"):
            resolve_project_dir(projects_dir, bad)


def test_resolve_project_dir_rejects_an_absolute_project_name(tmp_path):
    # The original bug: os.path.join(a, b) silently discards `a` when `b` is
    # absolute, so an unguarded join let this redirect the entire workspace.
    projects_dir = str(tmp_path / "projects")

    with pytest.raises(ConfigError, match="must be relative, not absolute"):
        resolve_project_dir(projects_dir, "/etc/cron.d")


def test_resolve_project_dir_rejects_dotdot_traversal(tmp_path):
    projects_dir = str(tmp_path / "projects")

    for traversal in ("..", "../escaped", "myapp/../../escaped", "a/b/../../../escaped"):
        with pytest.raises(ConfigError):
            resolve_project_dir(projects_dir, traversal)


def test_resolve_project_dir_rejects_a_symlinked_escape(tmp_path):
    # Even a project name with no ".." at all must be rejected if the
    # resulting path resolves, via an existing symlink, outside projects_dir.
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (projects_dir / "myapp").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ConfigError, match="escapes HARNESS_PROJECTS_DIR"):
        resolve_project_dir(str(projects_dir), "myapp")


def test_resolve_project_dir_allows_a_symlink_that_stays_inside(tmp_path):
    # A symlink is not inherently a violation — only one that resolves
    # outside projects_dir is. This one points at a sibling directory that
    # is still under projects_dir.
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    real_target = projects_dir / "real-team-dir"
    real_target.mkdir()
    (projects_dir / "myapp").symlink_to(real_target, target_is_directory=True)

    resolved = resolve_project_dir(str(projects_dir), "myapp")

    assert os.path.realpath(resolved) == os.path.realpath(str(real_target))


# --- Server-mode task registry persistence (task_store.py config) ----------


def test_task_store_defaults_to_memory_with_no_ttl():
    cfg = load_config(_base_env())

    assert cfg.task_store == DEFAULT_TASK_STORE == "memory"
    assert cfg.task_ttl_seconds == DEFAULT_TASK_TTL_SECONDS == 0


def test_task_store_accepts_each_backend():
    for backend in ("memory", "redis", "sqlite", "mysql", "postgres"):
        cfg = load_config(_base_env(HARNESS_TASK_STORE=backend))
        assert cfg.task_store == backend


def test_task_store_is_case_insensitive():
    cfg = load_config(_base_env(HARNESS_TASK_STORE="SQLITE"))
    assert cfg.task_store == "sqlite"


def test_invalid_task_store_raises():
    with pytest.raises(ConfigError, match="HARNESS_TASK_STORE"):
        load_config(_base_env(HARNESS_TASK_STORE="oracle"))


def test_task_ttl_seconds_zero_means_keep_forever():
    cfg = load_config(_base_env(HARNESS_TASK_TTL_SECONDS="0"))
    assert cfg.task_ttl_seconds == 0


def test_task_ttl_seconds_accepts_a_positive_value():
    cfg = load_config(_base_env(HARNESS_TASK_TTL_SECONDS="3600"))
    assert cfg.task_ttl_seconds == 3600


@pytest.mark.parametrize("bad", ["-1", "abc", "3.5"])
def test_invalid_task_ttl_seconds_raises(bad):
    with pytest.raises(ConfigError, match="HARNESS_TASK_TTL_SECONDS"):
        load_config(_base_env(HARNESS_TASK_TTL_SECONDS=bad))


def test_task_store_sqlite_path_defaults_and_overrides():
    cfg = load_config(_base_env())
    assert cfg.task_store_sqlite_path == DEFAULT_TASK_STORE_SQLITE_PATH

    cfg = load_config(_base_env(HARNESS_TASK_STORE_SQLITE_PATH="/tmp/custom.db"))
    assert cfg.task_store_sqlite_path == "/tmp/custom.db"


def test_task_store_mysql_fields_default_and_override():
    cfg = load_config(_base_env())
    assert cfg.task_store_mysql_host == "localhost"
    assert cfg.task_store_mysql_port == DEFAULT_TASK_STORE_MYSQL_PORT
    assert cfg.task_store_mysql_user == "root"
    assert cfg.task_store_mysql_password is None
    assert cfg.task_store_mysql_database == "harness"

    cfg = load_config(
        _base_env(
            HARNESS_TASK_STORE_MYSQL_HOST="db.internal",
            HARNESS_TASK_STORE_MYSQL_PORT="3307",
            HARNESS_TASK_STORE_MYSQL_USER="harness_user",
            HARNESS_TASK_STORE_MYSQL_PASSWORD="secret",
            HARNESS_TASK_STORE_MYSQL_DATABASE="harness_prod",
        )
    )
    assert cfg.task_store_mysql_host == "db.internal"
    assert cfg.task_store_mysql_port == 3307
    assert cfg.task_store_mysql_user == "harness_user"
    assert cfg.task_store_mysql_password == "secret"
    assert cfg.task_store_mysql_database == "harness_prod"


def test_invalid_task_store_mysql_port_raises():
    with pytest.raises(ConfigError, match="HARNESS_TASK_STORE_MYSQL_PORT"):
        load_config(_base_env(HARNESS_TASK_STORE_MYSQL_PORT="not-a-port"))


def test_task_store_postgres_fields_default_and_override():
    cfg = load_config(_base_env())
    assert cfg.task_store_postgres_host == "localhost"
    assert cfg.task_store_postgres_port == DEFAULT_TASK_STORE_POSTGRES_PORT
    assert cfg.task_store_postgres_user == "postgres"
    assert cfg.task_store_postgres_password is None
    assert cfg.task_store_postgres_database == "harness"

    cfg = load_config(
        _base_env(
            HARNESS_TASK_STORE_POSTGRES_HOST="pg.internal",
            HARNESS_TASK_STORE_POSTGRES_PORT="5433",
            HARNESS_TASK_STORE_POSTGRES_USER="harness_user",
            HARNESS_TASK_STORE_POSTGRES_PASSWORD="secret",
            HARNESS_TASK_STORE_POSTGRES_DATABASE="harness_prod",
        )
    )
    assert cfg.task_store_postgres_host == "pg.internal"
    assert cfg.task_store_postgres_port == 5433
    assert cfg.task_store_postgres_user == "harness_user"
    assert cfg.task_store_postgres_password == "secret"
    assert cfg.task_store_postgres_database == "harness_prod"


def test_task_store_redis_fields_default_and_override():
    cfg = load_config(_base_env())
    assert cfg.task_store_redis_host == "localhost"
    assert cfg.task_store_redis_port == DEFAULT_TASK_STORE_REDIS_PORT
    assert cfg.task_store_redis_db == 0
    assert cfg.task_store_redis_password is None
    assert cfg.task_store_redis_use_tls is False

    cfg = load_config(
        _base_env(
            HARNESS_TASK_STORE_REDIS_HOST="redis.internal",
            HARNESS_TASK_STORE_REDIS_PORT="6380",
            HARNESS_TASK_STORE_REDIS_DB="2",
            HARNESS_TASK_STORE_REDIS_PASSWORD="secret",
            HARNESS_TASK_STORE_REDIS_USE_TLS="true",
        )
    )
    assert cfg.task_store_redis_host == "redis.internal"
    assert cfg.task_store_redis_port == 6380
    assert cfg.task_store_redis_db == 2
    assert cfg.task_store_redis_password == "secret"
    assert cfg.task_store_redis_use_tls is True


@pytest.mark.parametrize("bad", ["-1", "abc"])
def test_invalid_task_store_redis_db_raises(bad):
    with pytest.raises(ConfigError, match="HARNESS_TASK_STORE_REDIS_DB"):
        load_config(_base_env(HARNESS_TASK_STORE_REDIS_DB=bad))


@pytest.mark.parametrize(
    "value,expected", [("true", True), ("1", True), ("yes", True), ("on", True)]
)
def test_task_store_redis_use_tls_accepts_true_spellings(value, expected):
    cfg = load_config(_base_env(HARNESS_TASK_STORE_REDIS_USE_TLS=value))
    assert cfg.task_store_redis_use_tls is expected


@pytest.mark.parametrize(
    "value,expected", [("false", False), ("0", False), ("no", False), ("off", False)]
)
def test_task_store_redis_use_tls_accepts_false_spellings(value, expected):
    cfg = load_config(_base_env(HARNESS_TASK_STORE_REDIS_USE_TLS=value))
    assert cfg.task_store_redis_use_tls is expected


def test_invalid_task_store_redis_use_tls_raises():
    with pytest.raises(ConfigError, match="HARNESS_TASK_STORE_REDIS_USE_TLS"):
        load_config(_base_env(HARNESS_TASK_STORE_REDIS_USE_TLS="maybe"))


# --- Interactive mode (HARNESS_INTERACTIVE) ---------------------------------


def test_interactive_defaults_to_false():
    cfg = load_config(_base_env())
    assert cfg.interactive is False


@pytest.mark.parametrize(
    "value,expected", [("true", True), ("1", True), ("yes", True), ("on", True)]
)
def test_interactive_accepts_true_spellings(value, expected):
    cfg = load_config(_base_env(HARNESS_INTERACTIVE=value))
    assert cfg.interactive is expected


@pytest.mark.parametrize(
    "value,expected", [("false", False), ("0", False), ("no", False), ("off", False)]
)
def test_interactive_accepts_false_spellings(value, expected):
    cfg = load_config(_base_env(HARNESS_INTERACTIVE=value))
    assert cfg.interactive is expected


def test_invalid_interactive_raises():
    with pytest.raises(ConfigError, match="HARNESS_INTERACTIVE"):
        load_config(_base_env(HARNESS_INTERACTIVE="maybe"))


# --- Deterministic auto model selection (HARNESS_MODEL_SELECTION) ----------


def test_model_selection_defaults_to_manual():
    cfg = load_config(_base_env())
    assert cfg.model_selection == "manual"
    assert cfg.models_file == "./models.yaml"


def test_model_selection_accepts_auto():
    cfg = load_config(_base_env(HARNESS_MODEL_SELECTION="auto"))
    assert cfg.model_selection == "auto"


def test_model_selection_is_case_insensitive():
    cfg = load_config(_base_env(HARNESS_MODEL_SELECTION="AUTO"))
    assert cfg.model_selection == "auto"


def test_invalid_model_selection_raises():
    with pytest.raises(ConfigError, match="HARNESS_MODEL_SELECTION"):
        load_config(_base_env(HARNESS_MODEL_SELECTION="sometimes"))


def test_models_file_defaults_and_override():
    cfg = load_config(_base_env(HARNESS_MODELS_FILE="./custom-models.yaml"))
    assert cfg.models_file == "./custom-models.yaml"


# --- Per-run artifacts directory (HARNESS_ARTIFACTS_DIR) --------------------


def test_artifacts_dir_defaults_to_blank():
    cfg = load_config(_base_env())
    assert cfg.artifacts_dir == ""


def test_artifacts_dir_override():
    cfg = load_config(_base_env(HARNESS_ARTIFACTS_DIR="./artifacts"))
    assert cfg.artifacts_dir == "./artifacts"


def test_artifacts_dir_blank_value_stays_blank():
    cfg = load_config(_base_env(HARNESS_ARTIFACTS_DIR="   "))
    assert cfg.artifacts_dir == ""
