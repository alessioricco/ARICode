"""End-to-end smoke test for the runner, plus unit tests for the harness-side
test-verification loop (`_verify_tests_and_retry`) — those need no LLM/
network, only a fake `Conversation`-like object and a monkeypatched
`_run_project_tests`.

The e2e test skips cleanly when no LLM key is configured, so CI passes
without secrets. When a key is present, it runs one tiny, cheap task against
whichever provider/model `.env` currently points at (proving the harness is
provider-agnostic: this test never hardcodes a model).
"""

from __future__ import annotations

import pytest

from harness import runner
from harness.config import Config, ConfigError, load_config
from harness.custom_tools.run_tests_tool import RunTestsObservation


def _config_available() -> bool:
    try:
        load_config()
    except ConfigError:
        return False
    return True


@pytest.mark.skipif(not _config_available(), reason="No LLM_MODEL/LLM_API_KEY configured in .env")
def test_run_task_creates_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    from harness.runner import run_task

    target = tmp_path / "HELLO.txt"
    messages = run_task(
        f"Create a file at the absolute path {target} containing the single "
        "line: hello. Then finish."
    )

    assert messages, "expected at least one captured message"
    assert target.exists()
    assert "hello" in target.read_text().lower()


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


def _result(
    exit_code: int, summary: str = "", output: str = "", check_kind: str = "pytest"
) -> RunTestsObservation:
    return RunTestsObservation(
        exit_code=exit_code, summary=summary, output=output, check_kind=check_kind
    )


class _FakeConversation:
    """Records send_message/run calls instead of driving a real LLM loop."""

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.run_calls = 0

    def send_message(self, message: str) -> None:
        self.sent_messages.append(message)

    def run(self) -> None:
        self.run_calls += 1


def test_verify_tests_never_mode_skips_entirely(monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: calls.__setitem__("count", calls["count"] + 1) or _result(1))

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(conversation, _cfg(verify_tests="never"), emitted.append)

    assert calls["count"] == 0
    assert conversation.run_calls == 0
    assert emitted == []


def test_verify_tests_passes_on_first_check_does_nothing(monkeypatch):
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: _result(0, "3 passed"))

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(conversation, _cfg(verify_tests="always"), emitted.append)

    assert conversation.run_calls == 0
    assert conversation.sent_messages == []
    assert emitted == []


def test_verify_tests_no_tests_collected_is_not_a_failure(monkeypatch):
    # pytest exit code 5: ran cleanly, found zero tests (non-Python project,
    # or one with no tests yet) — not something to retry over.
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: _result(5, "no tests ran"))

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(conversation, _cfg(verify_tests="always"), emitted.append)

    assert conversation.run_calls == 0
    assert emitted == []


def test_verify_tests_retries_then_succeeds(monkeypatch):
    results = iter([_result(1, "1 failed"), _result(0, "3 passed")])
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(
        conversation, _cfg(verify_tests="always", max_verify_retries=2), emitted.append
    )

    assert conversation.run_calls == 1
    assert len(conversation.sent_messages) == 1
    assert "still failing" in conversation.sent_messages[0]
    assert emitted == []  # recovered — no giving-up notice


def test_verify_tests_exhausts_retries_and_emits_giveup_notice(monkeypatch):
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: _result(1, "2 failed"))

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(
        conversation, _cfg(verify_tests="always", max_verify_retries=2), emitted.append
    )

    assert conversation.run_calls == 2
    assert len(conversation.sent_messages) == 2
    assert len(emitted) == 1
    notice_text = emitted[0].content[0].text
    assert "still failing" in notice_text
    assert "2 automated fix attempt" in notice_text


def test_verify_tests_inconclusive_result_is_not_treated_as_failure(monkeypatch):
    # _run_project_tests returns None when the suite can't even be invoked
    # (e.g. it hangs past its own timeout) — a verification-step glitch
    # shouldn't abort an otherwise-successful task.
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: None)

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(conversation, _cfg(verify_tests="always"), emitted.append)

    assert conversation.run_calls == 0
    assert emitted == []


def test_verification_followup_nudges_toward_minimal_edit():
    followup = runner._verification_followup(_result(1, "1 failed", "AssertionError: ..."))

    assert "minimal, targeted edit" in followup
    assert "rewriting the whole file" in followup


def test_verification_followup_calls_out_syntax_errors_specifically():
    # Regression guard: repeated verification retries on a real project saw
    # the agent do a full-file rewrite to "fix" an IndentationError, which
    # left a new orphaned fragment causing a *different* IndentationError on
    # the next check — file grew, bug moved, never actually fixed. See
    # runner.py's comment on _SYNTAX_ERROR_MARKERS.
    output = (
        '  File "hanoi.py", line 85\n'
        "    def move(self, from_rod: int, to_rod: int) -> bool:\n"
        "IndentationError: unexpected indent"
    )
    followup = runner._verification_followup(_result(2, "1 error", output))

    assert "syntax/indentation error, not a logic bug" in followup
    assert "py_compile" in followup


def test_verification_followup_omits_syntax_guidance_for_logic_failures():
    followup = runner._verification_followup(
        _result(1, "1 failed", "AssertionError: assert not True")
    )

    assert "syntax/indentation error" not in followup


# --- npm_build verification kind --------------------------------------------
#
# Regression coverage for the false-pass this generalization fixes: a
# scaffolded Vite/React project with a JSX parse error in App.tsx. Pytest-only
# verification treated it as "no tests collected" (exit 5 — not a failure);
# `npm run build` actually catches it.


def test_npm_build_success_is_not_a_failure():
    assert not runner._tests_are_failing(_result(0, "npm run build succeeded", check_kind="npm_build"))


def test_npm_build_nonzero_exit_is_a_failure():
    assert runner._tests_are_failing(
        _result(1, "npm run build failed (exit 1)", check_kind="npm_build")
    )


def test_inconclusive_none_kind_is_not_a_failure():
    # e.g. npm isn't on PATH, or no known project marker was found at all.
    assert not runner._tests_are_failing(_result(0, "npm not found on PATH", check_kind="none"))


def test_verify_tests_retries_on_a_failing_npm_build(monkeypatch):
    results = iter(
        [
            _result(1, "npm run build failed (exit 1)", "PARSE_ERROR: boom", check_kind="npm_build"),
            _result(0, "npm run build succeeded", check_kind="npm_build"),
        ]
    )
    monkeypatch.setattr(runner, "_run_project_tests", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(
        conversation, _cfg(verify_tests="always", max_verify_retries=2), emitted.append
    )

    assert conversation.run_calls == 1
    assert "still failing" in conversation.sent_messages[0]
    assert "npm run build" in conversation.sent_messages[0]
    assert emitted == []


def test_verify_tests_exhausts_retries_on_npm_build_says_build_not_tests(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_run_project_tests",
        lambda _dir: _result(1, "npm run build failed (exit 1)", check_kind="npm_build"),
    )

    conversation = _FakeConversation()
    emitted = []
    runner._verify_tests_and_retry(
        conversation, _cfg(verify_tests="always", max_verify_retries=1), emitted.append
    )

    notice_text = emitted[0].content[0].text
    assert "project's build is" in notice_text


def test_verification_followup_for_npm_build_gives_js_syntax_guidance():
    output = (
        "[plugin:vite:oxc] Transform failed with 1 error:\n"
        "[PARSE_ERROR] Adjacent JSX elements must be wrapped in an enclosing tag."
    )
    followup = runner._verification_followup(
        _result(1, "npm run build failed (exit 1)", output, check_kind="npm_build")
    )

    assert "JavaScript/TypeScript syntax or parse error" in followup
    assert "enclosing tag or fragment" in followup
    assert "npm run build" in followup
