"""llm_check.py + `harness-admin check-llm` tests. No network: `build_llm`
is monkeypatched to return a fake LLM, so only result shaping, error
classification, secret scrubbing, and CLI output/exit codes are exercised.
(The real call path was verified live — see ROADMAP.md.)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openhands.sdk import TextContent
from openhands.sdk.llm.exceptions.types import (
    LLMAuthenticationError,
    LLMContextWindowTooSmallError,
)

from harness import admin_cli, llm_check
from harness.config import Config

SECRET = "sk-super-secret-value"


def _cfg(**overrides) -> Config:
    base = {
        "model": "openai/gpt-4o",
        "api_key": SECRET,
        "base_url": None,
        "workspace": ".",
        "max_iterations": 10,
        "confirm_mode": "never",
        "execution": "local",
    }
    base.update(overrides)
    return Config(**base)


def _response(*texts, prompt=17, completion=4, cost=0.00001):
    return SimpleNamespace(
        message=SimpleNamespace(content=[TextContent(text=t) for t in texts]),
        metrics=SimpleNamespace(
            accumulated_token_usage=SimpleNamespace(
                prompt_tokens=prompt, completion_tokens=completion
            ),
            accumulated_cost=cost,
        ),
    )


class FakeLLM:
    def __init__(self, outcome):
        self.outcome = outcome
        self.copy_updates = None
        self.messages = None

    def model_copy(self, *, update):
        self.copy_updates = update
        return self

    def completion(self, *, messages):
        self.messages = messages
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _patch(monkeypatch, outcome) -> FakeLLM:
    fake = FakeLLM(outcome)
    monkeypatch.setattr(llm_check, "build_llm", lambda cfg, usage_id: fake)
    return fake


def test_successful_answer_reports_reply_tokens_and_cost(monkeypatch):
    fake = _patch(monkeypatch, _response("OK"))

    result = llm_check.check_llm(_cfg(), timeout=15)

    assert result.ok
    assert result.reply == "OK"
    assert (result.prompt_tokens, result.completion_tokens) == (17, 4)
    assert result.cost == 0.00001
    # Fail fast: no SDK retries, caller's timeout.
    assert fake.copy_updates == {"num_retries": 0, "timeout": 15}
    assert fake.messages[0].content[0].text == llm_check.CHECK_PROMPT


def test_answer_without_text_is_a_failure(monkeypatch):
    _patch(monkeypatch, _response("   "))

    result = llm_check.check_llm(_cfg())

    assert not result.ok
    assert result.error_kind == "no_reply"


def test_authentication_error_is_classified_with_a_hint_and_key_scrubbed(monkeypatch):
    _patch(monkeypatch, LLMAuthenticationError(f"Incorrect API key provided: {SECRET}"))

    result = llm_check.check_llm(_cfg())

    assert not result.ok
    assert result.error_kind == "authentication"
    assert "LLM_API_KEY" in result.hint
    assert SECRET not in result.error
    assert "***" in result.error


def test_unmapped_error_is_reported_not_raised(monkeypatch):
    _patch(monkeypatch, RuntimeError("something odd"))

    result = llm_check.check_llm(_cfg())

    assert (result.ok, result.error_kind, result.hint) == (False, "error", None)
    assert "RuntimeError: something odd" in result.error


def test_failure_while_building_the_llm_is_a_setup_failure(monkeypatch):
    def boom(cfg, usage_id):
        raise LLMContextWindowTooSmallError(8192, 16384)

    monkeypatch.setattr(llm_check, "build_llm", boom)

    result = llm_check.check_llm(_cfg(model="ollama/llama3"))

    assert not result.ok
    assert result.error_kind == "setup"
    assert "LLMContextWindowTooSmallError" in result.error


# --- harness-admin check-llm --------------------------------------------------


def _run(monkeypatch, argv, cfg=None):
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg or _cfg())
    with pytest.raises(SystemExit) as exc:
        admin_cli.main(["check-llm", *argv])
    return exc.value.code


def test_cli_success_exits_zero_and_prints_reply(monkeypatch, capsys):
    _patch(monkeypatch, _response("OK"))

    assert _run(monkeypatch, []) == 0
    out = capsys.readouterr().out
    assert "Checking openai/gpt-4o" in out
    assert "OK — answered" in out and "'OK'" in out
    assert "Tokens: 17 in / 4 out" in out


def test_cli_failure_exits_one_with_hint_and_never_prints_the_key(monkeypatch, capsys):
    _patch(monkeypatch, LLMAuthenticationError(f"bad key {SECRET}"))

    assert _run(monkeypatch, []) == 1
    out = capsys.readouterr().out
    assert "FAILED (authentication)" in out
    assert "Hint:" in out
    assert SECRET not in out


def test_cli_overrides_model_and_base_url_for_the_check(monkeypatch, capsys):
    seen = {}

    def build(cfg, usage_id):
        seen["cfg"] = cfg
        return FakeLLM(_response("OK"))

    monkeypatch.setattr(llm_check, "build_llm", build)

    assert _run(monkeypatch, ["--model", "anthropic/x", "--base-url", "http://h:1"]) == 0
    assert (seen["cfg"].model, seen["cfg"].base_url) == ("anthropic/x", "http://h:1")
    assert "Checking anthropic/x at http://h:1" in capsys.readouterr().out


def test_cli_zero_cost_is_shown_as_unknown(monkeypatch, capsys):
    _patch(monkeypatch, _response("OK", cost=0.0))

    assert _run(monkeypatch, []) == 0
    assert "unknown (no pricing data)" in capsys.readouterr().out
