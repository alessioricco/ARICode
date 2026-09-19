"""CLI tests. No LLM/network: load_config and run_task are monkeypatched so
these exercise only argument parsing and control flow.
"""

from __future__ import annotations

import urllib.error

import pytest

from harness import cli
from harness.config import Config, ConfigError
from harness.runner import CompletionContract, TaskOutcome, TaskResult


def _fake_result(messages=(), verification_state="verified", limitations=()):
    """Build the same `TaskResult` shape `run_task` now returns, for tests
    that monkeypatch `run_task` — `messages[-1]`/`if messages:` behave
    exactly like a plain list, plus `.outcome` for the new verification
    state the CLI now reports."""
    contract = CompletionContract(
        goal="", acceptance_criteria=[], verification_checks=[], limitations=list(limitations)
    )
    outcome = TaskOutcome(verification_state=verification_state, completion_contract=contract)
    return TaskResult(list(messages), outcome)


def _cfg(**overrides) -> Config:
    base = {
        "model": "openai/gpt-4o",
        "api_key": "key",
        "base_url": None,
        "workspace": ".",
        "max_iterations": 10,
        "confirm_mode": "never",
        "execution": "local",
    }
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

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["task"] = task
        return _fake_result([_FakeMessage("done")])

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

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--execution", "docker"])

    assert exit_code == 0
    assert calls["cfg"].execution == "docker"


def test_run_task_error_is_reported_and_exits_nonzero(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        raise RuntimeError("HARNESS_EXECUTION=docker requires the 'sandbox' extra")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--execution", "docker"])

    assert exit_code == 1
    assert "sandbox" in capsys.readouterr().err.lower()


def test_project_flag_creates_subfolder_and_overrides_workspace(monkeypatch, tmp_path):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        calls["project"] = project
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--project", "myapp"])

    expected = str(tmp_path / "myapp")
    assert exit_code == 0
    assert calls["cfg"].workspace == expected
    assert calls["project"] == "myapp"
    assert (tmp_path / "myapp").is_dir()


def test_project_flag_rejects_a_path_escape_attempt(monkeypatch, tmp_path, capsys):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_task should not be called")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(cli, "run_task", _fail_if_called)

    exit_code = cli.main(["do something", "--project", "../escaped"])

    assert exit_code == 1
    assert "Configuration error" in capsys.readouterr().err
    assert not (tmp_path.parent / "escaped").exists()


def test_local_execution_runs_task_and_prints_final_message(monkeypatch, capsys):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["task"] = task
        calls["cfg"] = cfg
        return _fake_result([_FakeMessage("all done")])

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
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(
        ["do something", "--project", "myapp", "--agents-md", "This project uses FastAPI."]
    )

    assert exit_code == 0
    assert (tmp_path / "myapp" / "AGENTS.md").read_text() == "This project uses FastAPI."


def test_model_flag_overrides_llm_model_without_touching_env(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

    monkeypatch.setattr(
        cli, "load_config", lambda: _cfg(model="anthropic/claude-sonnet-4-5-20250929")
    )
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--model", "openai/gpt-4o"])

    assert exit_code == 0
    assert calls["cfg"].model == "openai/gpt-4o"


def test_api_key_and_base_url_flags_override_config(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

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

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    assert exit_code == 0
    assert calls["cfg"].model == "openai/gpt-4o"
    assert calls["cfg"].api_key == "key"


def test_reasoning_effort_flag_overrides_config(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--reasoning-effort", "low"])

    assert exit_code == 0
    assert calls["cfg"].reasoning_effort == "low"
    # Untouched fields keep their configured values.
    assert calls["cfg"].model == "openai/gpt-4o"


def test_blank_model_override_reports_config_error(monkeypatch, capsys):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_task should not be called")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fail_if_called)

    exit_code = cli.main(["do something", "--model", "   "])

    assert exit_code == 1
    assert "Model override must not be blank" in capsys.readouterr().err


def test_prints_verification_state_and_exits_zero_when_verified(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result([_FakeMessage("all done")], verification_state="verified")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    assert exit_code == 0
    assert "Verification: verified" in capsys.readouterr().out


def test_exits_nonzero_when_verification_retries_are_exhausted(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("I gave up")],
            verification_state="retry_exhausted",
            limitations=["pytest: 2 failed"],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: retry_exhausted" in out
    assert "pytest: 2 failed" in out


def test_exits_nonzero_when_verification_keeps_timing_out(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("still going")],
            verification_state="timed_out",
            limitations=["cargo test: exceeded its 300s timeout and was killed"],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: timed_out" in out


def test_exits_nonzero_when_a_fix_attempt_made_no_progress(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result([_FakeMessage("tried a fix")], verification_state="no_progress")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: no_progress" in out


def test_exits_nonzero_when_task_tracker_left_incomplete(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("finished")],
            verification_state="incomplete",
            limitations=["The agent's own task_tracker list still shows unfinished item(s)"],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: incomplete" in out


def test_exits_nonzero_when_agent_got_stuck(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result([], verification_state="stuck")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    assert exit_code == 1
    assert "Verification: stuck" in capsys.readouterr().out


def test_exits_nonzero_when_task_budget_is_exhausted(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("ran out of time")],
            verification_state="budget_exhausted",
            limitations=[
                (
                    "The task's shared wall-clock budget (HARNESS_MAX_TASK_SECONDS=1800) "
                    "ran out before the task reached a normal finish, so its own "
                    "completion claim, if any, was not verified."
                )
            ],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: budget_exhausted" in out
    assert "HARNESS_MAX_TASK_SECONDS" in out


def test_inconclusive_verification_still_exits_zero_but_is_visible(monkeypatch, capsys):
    # Inconclusive isn't an error (nothing was proven broken), but it must
    # not look like a silent, confirmed success either.
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("done, probably")],
            verification_state="inconclusive",
            limitations=["No automated check could be run for this project."],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Verification: inconclusive" in out
    assert "No automated check could be run" in out


# --- --require-verification: opt-in strict treatment of "inconclusive" -----


def test_require_verification_flag_defaults_to_off(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["called"] = True
        return _fake_result(verification_state="inconclusive")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    assert calls["called"] is True
    assert exit_code == 0


def test_require_verification_flag_makes_unknown_project_type_a_failure(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            verification_state="inconclusive",
            limitations=["No automated check could be run for this project."],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--require-verification"])

    assert exit_code == 1
    assert "Verification: inconclusive" in capsys.readouterr().out


def test_require_verification_flag_makes_a_missing_tool_a_failure(monkeypatch):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            verification_state="inconclusive",
            limitations=["npm is required to verify this project but was not found on PATH."],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--require-verification"])

    assert exit_code == 1


def test_require_verification_flag_makes_no_tests_collected_a_failure(monkeypatch):
    # Even the quiet, otherwise-non-blocking "pytest collected zero tests"
    # case is still just "inconclusive" — --require-verification doesn't
    # special-case it, since the whole point of opting in is "no evidence
    # is not good enough," regardless of which specific reason produced it.
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(verification_state="inconclusive", limitations=[])

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--require-verification"])

    assert exit_code == 1


def test_require_verification_flag_does_not_affect_a_real_verified_pass(monkeypatch):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(verification_state="verified")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--require-verification"])

    assert exit_code == 0


def test_require_verification_flag_does_not_change_other_failure_states(monkeypatch):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(verification_state="retry_exhausted")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--require-verification"])

    assert exit_code == 1


def test_execution_flag_overrides_configured_docker_default(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(execution="docker"))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something", "--execution", "local"])

    assert exit_code == 0
    assert calls["cfg"].execution == "local"


# --- HARNESS_CONFIRM_MODE=always wiring -------------------------------------


def test_exits_nonzero_when_confirmation_required_with_no_handler(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("stopped")],
            verification_state="confirmation_required",
            limitations=["The agent proposed an action that required confirmation"],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: confirmation_required" in out


def test_confirm_mode_never_passes_no_confirm_handler_to_run_task(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["on_confirm"] = on_confirm
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(confirm_mode="never"))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something"])

    assert calls["on_confirm"] is None


def test_confirm_mode_always_passes_the_interactive_handler_to_run_task(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["on_confirm"] = on_confirm
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(confirm_mode="always"))
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something"])

    assert calls["on_confirm"] is cli._confirm_pending_actions


def test_confirm_pending_actions_approves_only_on_explicit_yes(monkeypatch):
    from types import SimpleNamespace

    pending = [SimpleNamespace(tool_name="terminal", action="rm -rf /tmp/x")]

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert cli._confirm_pending_actions(pending) is True

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert cli._confirm_pending_actions(pending) is True

    for reply in ("n", "no", "", "sure", "YOLO"):
        monkeypatch.setattr("builtins.input", lambda _prompt, reply=reply: reply)
        assert cli._confirm_pending_actions(pending) is False


# --- --interactive: opt-in interactive checkpoint ---------------------------


def test_interactive_flag_off_by_default_passes_no_callback(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        calls["on_awaiting_input"] = on_awaiting_input
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something"])

    assert calls["cfg"].interactive is False
    assert calls["on_awaiting_input"] is None


def test_interactive_flag_sets_config_and_passes_the_terminal_handler(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        calls["on_awaiting_input"] = on_awaiting_input
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something", "--interactive"])

    assert calls["cfg"].interactive is True
    assert calls["on_awaiting_input"] is cli._prompt_for_continuation


def test_prompt_for_continuation_prints_narrative_and_returns_the_reply(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "please also add tests")

    reply = cli._prompt_for_continuation("Anything else you'd like?")

    assert reply == "please also add tests"
    assert "Anything else you'd like?" in capsys.readouterr().out


def test_prompt_for_continuation_returns_none_on_empty_reply(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "   ")

    assert cli._prompt_for_continuation("some narrative") is None


def test_prompt_for_continuation_handles_empty_narrative(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    reply = cli._prompt_for_continuation("")

    assert reply is None
    assert capsys.readouterr().out == ""  # nothing to show, no blank block printed


# --- --auto-model: opt-in deterministic model selection ---------------------


def test_auto_model_flag_off_by_default(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        calls["on_model_choice"] = on_model_choice
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something"])

    assert calls["cfg"].model_selection == "manual"
    assert calls["on_model_choice"] is None


def test_auto_model_flag_sets_config(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["cfg"] = cfg
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something", "--auto-model"])

    assert calls["cfg"].model_selection == "auto"


def test_auto_model_only_passes_the_prompt_when_also_interactive(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["on_model_choice"] = on_model_choice
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something", "--auto-model"])
    assert calls["on_model_choice"] is None  # auto-model without --interactive: no prompt

    cli.main(["do something", "--auto-model", "--interactive"])
    assert calls["on_model_choice"] is cli._prompt_for_model_choice


def test_prompt_for_model_choice_accepts_the_recommendation_on_enter(monkeypatch, capsys):
    from harness.model_catalog import ModelCatalogEntry

    candidates = [
        ModelCatalogEntry(name="cheap", model="a", ratings={"cost": 5}, description="Fast."),
        ModelCatalogEntry(name="strong", model="b", ratings={"reasoning": 5}),
    ]
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    choice = cli._prompt_for_model_choice(candidates, 0, "Task classified as 'default'.")

    assert choice == 0
    out = capsys.readouterr().out
    assert "cheap" in out
    assert "strong" in out
    assert "Fast." in out


def test_prompt_for_model_choice_accepts_a_typed_index(monkeypatch):
    from harness.model_catalog import ModelCatalogEntry

    candidates = [
        ModelCatalogEntry(name="cheap", model="a", ratings={}),
        ModelCatalogEntry(name="strong", model="b", ratings={}),
    ]
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    choice = cli._prompt_for_model_choice(candidates, 0, "reason")

    assert choice == 1


def test_prompt_for_model_choice_falls_back_to_recommended_on_invalid_input(monkeypatch):
    from harness.model_catalog import ModelCatalogEntry

    candidates = [ModelCatalogEntry(name="only", model="a", ratings={})]
    monkeypatch.setattr("builtins.input", lambda _prompt: "not a number")

    assert cli._prompt_for_model_choice(candidates, 0, "reason") == 0


def test_prompt_for_model_choice_falls_back_to_recommended_on_out_of_range_index(monkeypatch):
    from harness.model_catalog import ModelCatalogEntry

    candidates = [ModelCatalogEntry(name="only", model="a", ratings={})]
    monkeypatch.setattr("builtins.input", lambda _prompt: "99")

    assert cli._prompt_for_model_choice(candidates, 0, "reason") == 0


# --- --acceptance-checks: opt-in machine-checkable acceptance criteria ------


def test_resolve_acceptance_checks_parses_inline_json():
    checks = cli.resolve_acceptance_checks('[{"kind": "file_exists", "path": "README.md"}]')

    assert len(checks) == 1
    assert checks[0].kind == "file_exists"
    assert checks[0].path == "README.md"


def test_resolve_acceptance_checks_reads_a_file(tmp_path):
    checks_file = tmp_path / "checks.json"
    checks_file.write_text('[{"kind": "file_exists", "path": "OUTPUT.txt"}]')

    checks = cli.resolve_acceptance_checks(str(checks_file))

    assert checks[0].path == "OUTPUT.txt"


def test_resolve_acceptance_checks_rejects_invalid_json():
    with pytest.raises(ValueError, match="must be valid JSON"):
        cli.resolve_acceptance_checks("not json")


def test_resolve_acceptance_checks_rejects_a_non_array():
    with pytest.raises(ValueError, match="must be a JSON array"):
        cli.resolve_acceptance_checks('{"kind": "file_exists", "path": "x"}')


def test_resolve_acceptance_checks_rejects_an_invalid_check():
    with pytest.raises(ValueError, match="Unknown acceptance check kind"):
        cli.resolve_acceptance_checks('[{"kind": "run_command", "path": "x"}]')


def test_main_rejects_bad_acceptance_checks_json(monkeypatch, capsys):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_task should not be called")

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fail_if_called)

    exit_code = cli.main(["do something", "--acceptance-checks", "not json"])

    assert exit_code == 1
    assert "Configuration error" in capsys.readouterr().err


def test_main_passes_parsed_acceptance_checks_to_run_task(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["acceptance_checks"] = acceptance_checks
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(
        [
            "do something",
            "--acceptance-checks",
            '[{"kind": "file_exists", "path": "OUTPUT.txt"}]',
        ]
    )

    assert exit_code == 0
    assert calls["acceptance_checks"][0].path == "OUTPUT.txt"


def test_main_without_acceptance_checks_flag_passes_none(monkeypatch):
    calls = {}

    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        calls["acceptance_checks"] = acceptance_checks
        return _fake_result()

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    cli.main(["do something"])

    assert calls["acceptance_checks"] is None


def test_exits_nonzero_when_acceptance_check_failed(monkeypatch, capsys):
    def _fake_run_task(
        task,
        cfg=None,
        on_confirm=None,
        acceptance_checks=None,
        on_awaiting_input=None,
        on_model_choice=None,
        run_id=None,
        project=None,
    ):
        return _fake_result(
            [_FakeMessage("done, I think")],
            verification_state="acceptance_failed",
            limitations=[
                "Required acceptance check failed (OUTPUT.txt): OUTPUT.txt does not exist"
            ],
        )

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(cli, "run_task", _fake_run_task)

    exit_code = cli.main(["do something"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Verification: acceptance_failed" in out
