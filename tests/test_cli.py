"""CLI tests. No LLM/network: load_config and run_task are monkeypatched so
these exercise only argument parsing and control flow.
"""

from __future__ import annotations

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
