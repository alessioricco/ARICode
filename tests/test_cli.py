"""CLI tests. No LLM/network: load_config and run_task are monkeypatched so
these exercise only argument parsing and control flow.
"""

from __future__ import annotations

import urllib.error

import pytest

from harness import cli
from harness.config import Config, ConfigError


def _cfg(**overrides) -> Config:
    base = dict(
        model="openai/gpt-4o",
        api_key="key",
        base_url=None,
        workspace=".",
        max_iterations=10,
        confirm_mode="never",
        execution="local",
    )
    base.update(overrides)
    return Config(**base)


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeContent(text)]


class _FakeUrlResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return self._body


def test_resolve_task_source_returns_literal_text_unchanged():
    assert cli.resolve_task_source("Refactor utils.py") == "Refactor utils.py"


def test_resolve_task_source_reads_existing_file(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("Do the thing described here.\n")

    assert cli.resolve_task_source(str(task_file)) == "Do the thing described here."


def test_resolve_task_source_fetches_url(monkeypatch):
    monkeypatch.setattr(
        cli.urllib.request,
        "urlopen",
        lambda url, timeout: _FakeUrlResponse(b"Task fetched from the web."),
    )

    assert cli.resolve_task_source("https://example.com/task.md") == "Task fetched from the web."


def test_resolve_task_source_url_fetch_error_raises_value_error(monkeypatch):
    def _raise(url, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(cli.urllib.request, "urlopen", _raise)

    with pytest.raises(ValueError, match="Failed to fetch task from URL"):
        cli.resolve_task_source("https://example.com/task.md")


def test_resolve_task_source_empty_file_raises_value_error(tmp_path):
    task_file = tmp_path / "empty.md"
    task_file.write_text("   \n")

    with pytest.raises(ValueError, match="is empty"):
        cli.resolve_task_source(str(task_file))


def test_main_reads_task_from_file(monkeypatch, tmp_path, capsys):
    task_file = tmp_path / "task.md"
    task_file.write_text("Create HELLO.txt with the line: hi.")
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["task"] = task
        return [_FakeMessage("done")]

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main([str(task_file)])

    assert exit_code == 0
    assert calls["task"] == "Create HELLO.txt with the line: hi."


def test_main_reports_task_resolution_error(monkeypatch, capsys):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_task should not be called")

    def _raise(url, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(cli.urllib.request, "urlopen", _raise)
    monkeypatch.setattr(cli, "run_task", _fail_if_called)

    exit_code = cli.main(["https://example.com/task.md"])

    assert exit_code == 1
    assert "Failed to fetch task from URL" in capsys.readouterr().err


def test_config_error_is_reported_and_exits_nonzero(monkeypatch, capsys):
    def _raise() -> Config:
        raise ConfigError("LLM_MODEL is required")

    monkeypatch.setattr(cli, "load_config", _raise)

    exit_code = cli.main(["do something"])

    assert exit_code == 1
    assert "LLM_MODEL is required" in capsys.readouterr().err


def test_docker_execution_is_passed_through_to_run_task(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--execution", "docker"])

    assert exit_code == 0
    assert calls["cfg"].execution == "docker"


def test_run_task_error_is_reported_and_exits_nonzero(monkeypatch, capsys):
    def _fake_run_task(task, cfg=None):
        raise RuntimeError("HARNESS_EXECUTION=docker requires the 'sandbox' extra")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--execution", "docker"])

    assert exit_code == 1
    assert "sandbox" in capsys.readouterr().err.lower()


def test_project_flag_creates_subfolder_and_overrides_workspace(monkeypatch, tmp_path):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--project", "myapp"])

    expected = str(tmp_path / "myapp")
    assert exit_code == 0
    assert calls["cfg"].workspace == expected
    assert (tmp_path / "myapp").is_dir()


def test_local_execution_runs_task_and_prints_final_message(monkeypatch, capsys):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["task"] = task
        calls["cfg"] = cfg
        return [_FakeMessage("all done")]

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    assert exit_code == 0
    assert calls["task"] == "do something"
    assert calls["cfg"].execution == "local"
    assert "all done" in capsys.readouterr().out


def test_agents_md_without_project_is_rejected(monkeypatch, capsys):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_task should not be called")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fail_if_called)

    exit_code = cli.main(["do something", "--agents-md", "some content"])

    assert exit_code == 1
    assert "--agents-md requires --project" in capsys.readouterr().err


def test_agents_md_with_project_writes_file(monkeypatch, tmp_path):
    def _fake_run_task(task, cfg=None):
        return []

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(
        ["do something", "--project", "myapp", "--agents-md", "This project uses FastAPI."]
    )

    assert exit_code == 0
    assert (tmp_path / "myapp" / "AGENTS.md").read_text() == "This project uses FastAPI."


def test_model_flag_overrides_llm_model_without_touching_env(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(
        cli, "load_config", lambda: _cfg(model="anthropic/claude-sonnet-4-5-20250929")
    )
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--model", "openai/gpt-4o"])

    assert exit_code == 0
    assert calls["cfg"].model == "openai/gpt-4o"


def test_api_key_and_base_url_flags_override_config(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(
        [
            "do something",
            "--api-key",
            "sk-other",
            "--base-url",
            "http://localhost:11434",
        ]
    )

    assert exit_code == 0
    assert calls["cfg"].api_key == "sk-other"
    assert calls["cfg"].base_url == "http://localhost:11434"


def test_no_llm_override_flags_leaves_config_untouched(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    assert exit_code == 0
    assert calls["cfg"].model == "openai/gpt-4o"
    assert calls["cfg"].api_key == "key"


def test_blank_model_override_reports_config_error(monkeypatch, capsys):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_task should not be called")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fail_if_called)

    exit_code = cli.main(["do something", "--model", "   "])

    assert exit_code == 1
    assert "Model override must not be blank" in capsys.readouterr().err


def test_execution_flag_overrides_configured_docker_default(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(execution="docker"))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--execution", "local"])

    assert exit_code == 0
    assert calls["cfg"].execution == "local"
