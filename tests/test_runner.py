"""End-to-end smoke test for the runner, plus unit tests for the harness-side
verification loop (`_verify_and_report`) — those need no LLM/network, only a
fake `Conversation`-like object and a monkeypatched `_run_verification`.

The e2e test skips cleanly when no LLM key is configured, so CI passes
without secrets. When a key is present, it runs one tiny, cheap task against
whichever provider/model `.env` currently points at (proving the harness is
provider-agnostic: this test never hardcodes a model).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from openhands.sdk import ConversationExecutionStatus

from harness import runner
from harness.config import Config, ConfigError, load_config
from harness.custom_tools.run_tests_tool import CheckOutcome, VerificationRun


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
    assert messages.outcome.verification_state in runner.VERIFICATION_STATES


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


def _check(
    name: str = "pytest",
    command: str = "pytest -q",
    *,
    primary: bool = True,
    status: str = "passed",
    exit_code: int | None = 0,
    summary: str = "",
    output: str = "",
) -> CheckOutcome:
    return CheckOutcome(
        name=name,
        command=command,
        primary=primary,
        status=status,
        exit_code=exit_code,
        project_root="/tmp/x",
        summary=summary,
        output=output,
    )


def _run(*checks: CheckOutcome) -> VerificationRun:
    return VerificationRun(checks=list(checks))


class _FakeConversation:
    """Records send_message/run calls instead of driving a real LLM loop.

    `initial_status` is the execution_status `_verify_and_report` sees before
    doing anything (i.e. the result of the harness's own already-completed
    first `conversation.run()`). `statuses_after_run`, if given, is consumed
    one value per subsequent `.run()` call (a retry) to simulate the SDK
    transitioning into a stuck/error state mid-retry; once exhausted, status
    stays whatever it last was — a healthy retry that doesn't get stuck.
    """

    def __init__(
        self,
        initial_status: ConversationExecutionStatus = ConversationExecutionStatus.FINISHED,
        statuses_after_run: list[ConversationExecutionStatus] | None = None,
    ) -> None:
        self.sent_messages: list[str] = []
        self.run_calls = 0
        self.state = SimpleNamespace(execution_status=initial_status)
        self._statuses_after_run = list(statuses_after_run or [])

    def send_message(self, message: str) -> None:
        self.sent_messages.append(message)

    def run(self) -> None:
        self.run_calls += 1
        if self._statuses_after_run:
            self.state.execution_status = self._statuses_after_run.pop(0)


# --- verify_tests=never: verification is skipped entirely ------------------


def test_verify_tests_never_mode_skips_entirely(monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: calls.__setitem__("count", calls["count"] + 1) or _run(),
    )

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="never"), emitted.append, "do the thing"
    )

    assert calls["count"] == 0
    assert conversation.run_calls == 0
    assert emitted == []
    assert outcome.verification_state == "inconclusive"
    assert any("skipped" in note for note in outcome.completion_contract.limitations)


# --- successful verification ------------------------------------------------


def test_verify_tests_passes_on_first_check_reports_verified(monkeypatch):
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: _run(_check(summary="3 passed")))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 0
    assert conversation.sent_messages == []
    assert emitted == []
    assert outcome.verification_state == "verified"
    assert outcome.success is True
    assert outcome.retries_used == 0
    assert "pytest -q" in outcome.completion_contract.verification_checks


# --- inconclusive verification ----------------------------------------------


def test_no_tests_collected_is_inconclusive_not_verified_and_stays_silent(monkeypatch):
    # The one exemption req'd to keep pre-existing behavior (see MANUAL.md
    # "Test verification"): pytest ran cleanly and found zero tests. Not a
    # failure, but also not proof the software works — must report
    # "inconclusive", not "verified", while keeping the original quiet,
    # non-blocking behavior for this specific, well-defined signal.
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(_check(status="skipped", exit_code=5, summary="no tests ran")),
    )

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 0
    assert emitted == []
    assert outcome.verification_state == "inconclusive"
    assert outcome.success is False


def test_no_runnable_check_is_inconclusive_and_visibly_flagged(monkeypatch):
    # Unlike the clean "no tests collected" case above, a verification pass
    # that produced no checks at all (e.g. run_full_verification raised and
    # _run_verification swallowed it) is a genuine "we don't know" and must
    # be visible, not silently treated as fine.
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: _run())

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 0
    assert len(emitted) == 1
    assert "not independently verified" in emitted[0].content[0].text
    assert outcome.verification_state == "inconclusive"


def test_run_verification_swallows_a_verification_glitch(monkeypatch):
    # A hung/erroring check must not abort an otherwise-successful task.
    def _boom(_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "run_full_verification", _boom)

    result = runner._run_verification("/some/dir")

    assert result.checks == []
    assert result.state == "inconclusive"


# --- failing verification, then a successful retry --------------------------


def test_verify_tests_retries_then_succeeds(monkeypatch):
    results = iter(
        [
            _run(
                _check(
                    status="failed", exit_code=1, summary="1 failed", output="AssertionError: boom"
                )
            ),
            _run(_check(status="passed", summary="3 passed")),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=2),
        emitted.append,
        "do the thing",
    )

    assert conversation.run_calls == 1
    assert len(conversation.sent_messages) == 1
    assert "still failing" in conversation.sent_messages[0]
    assert emitted == []  # recovered — no giving-up notice
    assert outcome.verification_state == "verified"
    assert outcome.retries_used == 1


# --- retry exhaustion --------------------------------------------------------


def test_verify_tests_exhausts_retries_and_emits_giveup_notice(monkeypatch):
    # Each attempt fails differently (a distinct output each time) so this
    # exercises genuine exhaustion, not the separate "no_progress" path
    # (identical failure signature) covered below.
    results = iter(
        [
            _run(
                _check(
                    status="failed", exit_code=1, summary="2 failed", output="AssertionError: one"
                )
            ),
            _run(
                _check(
                    status="failed", exit_code=1, summary="2 failed", output="AssertionError: two"
                )
            ),
            _run(
                _check(
                    status="failed", exit_code=1, summary="2 failed", output="AssertionError: three"
                )
            ),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=2),
        emitted.append,
        "do the thing",
    )

    assert conversation.run_calls == 2
    assert len(conversation.sent_messages) == 2
    assert len(emitted) == 1
    notice_text = emitted[0].content[0].text
    assert "still failing" in notice_text
    assert "2 automated fix attempt" in notice_text
    assert outcome.verification_state == "retry_exhausted"
    assert outcome.retries_used == 2
    assert outcome.success is False


# --- agent stuck / aborted ---------------------------------------------------


def test_stuck_before_verification_skips_checks_entirely(monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: calls.__setitem__("count", calls["count"] + 1) or _run(),
    )

    conversation = _FakeConversation(initial_status=ConversationExecutionStatus.STUCK)
    emitted = []
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), emitted.append, "do the thing"
    )

    assert calls["count"] == 0  # never even tried to verify a stuck run
    assert conversation.run_calls == 0
    assert len(emitted) == 1
    assert "stuck" in emitted[0].content[0].text.lower()
    assert outcome.verification_state == "stuck"
    assert outcome.success is False
    assert any(
        "did not reach a normal finish" in note for note in outcome.completion_contract.limitations
    )


def test_error_status_before_verification_is_also_reported_as_stuck(monkeypatch):
    conversation = _FakeConversation(initial_status=ConversationExecutionStatus.ERROR)
    emitted = []
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), emitted.append, "do the thing"
    )

    assert outcome.verification_state == "stuck"
    assert "'error'" in emitted[0].content[0].text


def test_agent_gets_stuck_mid_retry(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(_check(status="failed", exit_code=1, summary="1 failed")),
    )

    conversation = _FakeConversation(statuses_after_run=[ConversationExecutionStatus.STUCK])
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=2),
        emitted.append,
        "do the thing",
    )

    assert conversation.run_calls == 1  # got stuck on the first retry, no second attempt
    assert len(emitted) == 1
    assert "stuck" in emitted[0].content[0].text.lower()
    assert outcome.verification_state == "stuck"
    assert outcome.retries_used == 1


# --- completion contract -----------------------------------------------------


def test_completion_contract_carries_the_goal_and_checks(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(_check(command="pytest -q", summary="3 passed")),
    )

    conversation = _FakeConversation()
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), lambda _m: None, "build a CLI"
    )

    contract = outcome.completion_contract
    assert contract.goal == "build a CLI"
    assert "pytest -q" in contract.verification_checks
    assert any("pytest -q" in c for c in contract.acceptance_criteria)


def test_completion_contract_notes_limitations_from_skipped_checks(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(
            _check(summary="3 passed"),
            _check(
                name="ruff",
                command="ruff check .",
                primary=False,
                status="unavailable",
                exit_code=None,
                summary="ruff is configured but not installed; lint was not verified",
            ),
        ),
    )

    conversation = _FakeConversation()
    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), lambda _m: None, "do the thing"
    )

    assert (
        outcome.verification_state == "verified"
    )  # primary passed; ruff being unavailable doesn't block
    assert any(
        "ruff" in note and "not installed" in note
        for note in outcome.completion_contract.limitations
    )


# --- _verification_followup composes guidance across failing checks --------


def test_verification_followup_nudges_toward_minimal_edit():
    followup = runner._verification_followup(
        _run(_check(status="failed", exit_code=1, summary="1 failed", output="AssertionError: ..."))
    )

    assert "minimal, targeted edit" in followup
    assert "rewriting the whole file" in followup


def test_verification_followup_calls_out_python_syntax_errors_specifically():
    # Regression guard: repeated verification retries on a real project saw
    # the agent do a full-file rewrite to "fix" an IndentationError, which
    # left a new orphaned fragment causing a *different* IndentationError on
    # the next check — file grew, bug moved, never actually fixed.
    output = (
        '  File "hanoi.py", line 85\n'
        "    def move(self, from_rod: int, to_rod: int) -> bool:\n"
        "IndentationError: unexpected indent"
    )
    followup = runner._verification_followup(
        _run(_check(status="failed", exit_code=1, summary="1 error", output=output))
    )

    assert "syntax/indentation error, not a logic bug" in followup
    assert "py_compile" in followup


def test_verification_followup_calls_out_js_syntax_errors_specifically():
    output = (
        "[plugin:vite:oxc] Transform failed with 1 error:\n"
        "[PARSE_ERROR] Adjacent JSX elements must be wrapped in an enclosing tag."
    )
    followup = runner._verification_followup(
        _run(
            _check(
                name="npm run build",
                command="npm run build",
                status="failed",
                exit_code=1,
                summary="build failed",
                output=output,
            )
        )
    )

    assert "JavaScript/TypeScript syntax or parse error" in followup
    assert "enclosing tag or fragment" in followup


def test_verification_followup_omits_syntax_guidance_for_logic_failures():
    followup = runner._verification_followup(
        _run(
            _check(
                status="failed",
                exit_code=1,
                summary="1 failed",
                output="AssertionError: assert not True",
            )
        )
    )

    assert "syntax/indentation error" not in followup


def test_verification_followup_includes_every_failing_check():
    followup = runner._verification_followup(
        _run(
            _check(
                name="pytest",
                status="failed",
                exit_code=1,
                summary="1 failed",
                output="AssertionError",
            ),
            _check(
                name="ruff",
                command="ruff check .",
                primary=False,
                status="failed",
                exit_code=1,
                summary="1 error",
                output="E501",
            ),
        )
    )

    assert "pytest" in followup
    assert "ruff" in followup
    assert "E501" in followup


def test_verification_followup_includes_timed_out_checks_too():
    followup = runner._verification_followup(
        _run(
            _check(
                name="cargo test",
                command="cargo test",
                status="timed_out",
                exit_code=None,
                summary="cargo test exceeded its 300s timeout and was killed",
                output="",
            )
        )
    )

    assert "cargo test" in followup
    assert "hanging" in followup.lower() or "hang" in followup.lower()
    assert "do not simply increase a timeout" in followup


# --- timed_out state and retry behavior -------------------------------------


def test_verification_run_state_timed_out_triggers_retry_and_terminal_timed_out(monkeypatch):
    # Two distinct timeouts (different partial output each time) so this
    # exercises genuine repeated-timeout exhaustion, not "no_progress"
    # (identical signature) covered separately below.
    results = iter(
        [
            _run(
                _check(
                    name="cargo test",
                    command="cargo test",
                    status="timed_out",
                    exit_code=None,
                    output="a",
                )
            ),
            _run(
                _check(
                    name="cargo test",
                    command="cargo test",
                    status="timed_out",
                    exit_code=None,
                    output="b",
                )
            ),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=1),
        emitted.append,
        "do the thing",
    )

    assert conversation.run_calls == 1  # retried once, per max_verify_retries=1
    assert len(emitted) == 1
    assert outcome.verification_state == "timed_out"  # not "retry_exhausted" — a distinct problem
    assert outcome.retries_used == 1


def test_timed_out_check_recovers_on_retry_reports_verified(monkeypatch):
    results = iter(
        [
            _run(
                _check(name="cargo test", command="cargo test", status="timed_out", exit_code=None)
            ),
            _run(_check(name="cargo test", command="cargo test", status="passed")),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=2),
        emitted.append,
        "do the thing",
    )

    assert conversation.run_calls == 1
    assert emitted == []
    assert outcome.verification_state == "verified"
    assert outcome.retries_used == 1


# --- no_progress: a fix attempt that changed nothing observable ------------


def test_no_progress_detected_when_fix_attempt_changes_nothing(monkeypatch):
    # Regression case: an agent edited a comparison operator to "fix" a
    # failing test, but the edit was a no-op for that failure (a different
    # branch handled it) — pytest's output was identical before and after.
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(
            _check(status="failed", exit_code=1, summary="1 failed", output="AssertionError: boom")
        ),
    )

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=5),
        emitted.append,
        "do the thing",
    )

    # Stopped after exactly one retry — well before the 5-attempt budget —
    # because that one retry provably changed nothing.
    assert conversation.run_calls == 1
    assert outcome.verification_state == "no_progress"
    assert outcome.retries_used == 1
    assert outcome.success is False
    assert len(emitted) == 1
    notice_text = emitted[0].content[0].text
    assert "no observable change" in notice_text
    assert "1 failed" in notice_text


def test_no_progress_ignores_timing_only_differences(monkeypatch):
    # Two runs whose only difference is a wall-clock duration in the output
    # — must still count as "no progress", not a genuine change.
    results = iter(
        [
            _run(_check(status="failed", exit_code=1, summary="1 failed in 3.85s", output="boom")),
            _run(_check(status="failed", exit_code=1, summary="1 failed in 3.83s", output="boom")),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=5),
        emitted.append,
        "do the thing",
    )

    assert outcome.verification_state == "no_progress"
    assert outcome.retries_used == 1


def test_progress_with_a_genuinely_different_failure_does_not_trigger_no_progress(monkeypatch):
    # A different failure each time is real (if unresolved) progress, not
    # "no progress" — must not be misclassified.
    results = iter(
        [
            _run(
                _check(
                    status="failed",
                    exit_code=1,
                    summary="1 failed",
                    output="AssertionError: first bug",
                )
            ),
            _run(
                _check(
                    status="failed",
                    exit_code=1,
                    summary="1 failed",
                    output="AssertionError: second bug",
                )
            ),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=1),
        emitted.append,
        "do the thing",
    )

    assert outcome.verification_state == "retry_exhausted"
    assert conversation.run_calls == 1


def test_no_progress_applies_to_repeated_identical_timeouts_too(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(
            _check(
                name="cargo test",
                command="cargo test",
                status="timed_out",
                exit_code=None,
                output="hang",
            )
        ),
    )

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=5),
        emitted.append,
        "do the thing",
    )

    assert outcome.verification_state == "no_progress"
    assert outcome.retries_used == 1


# --- max_iteration_per_run is actually wired to Conversation ---------------


class _FakeWorkspaceCM:
    """Minimal context manager standing in for `build_workspace`'s return
    value (`nullcontext(...)` for local execution, `DockerWorkspace` for
    docker) — only `__enter__`/`__exit__` matter here.
    """

    def __init__(self, value: str) -> None:
        self._value = value

    def __enter__(self) -> str:
        return self._value

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _RecordingConversation:
    """Stands in for `openhands.sdk.Conversation`, recording the kwargs each
    instance was constructed with (via the class-level `instances` list)
    instead of driving a real LLM loop.
    """

    instances: ClassVar[list[_RecordingConversation]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.state = SimpleNamespace(execution_status=ConversationExecutionStatus.FINISHED)
        _RecordingConversation.instances.append(self)

    def send_message(self, message: str) -> None:
        pass

    def run(self) -> None:
        pass


def test_stream_task_wires_max_iterations_to_conversation(monkeypatch):
    # Regression guard: HARNESS_MAX_ITERATIONS was parsed/validated by
    # config.py from Milestone 1 onward but never actually passed to
    # Conversation(...), so every run silently used the SDK's own default
    # (500) regardless of .env — contrary to docs/SPEC.md section 12's
    # acceptance criterion ("HARNESS_MAX_ITERATIONS reliably bounds runaway
    # loops"). Found only by reading runner.py directly, not by any test —
    # this asserts the kwarg is actually threaded through, so a future
    # regression (e.g. an SDK upgrade renaming/removing the kwarg) fails
    # loudly here instead of silently reverting to an unbounded default.
    _RecordingConversation.instances = []
    monkeypatch.setattr(runner, "Conversation", _RecordingConversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: _FakeWorkspaceCM("fake-workspace"))

    outcome = runner.stream_task("do the thing", cfg=_cfg(verify_tests="never", max_iterations=17))

    assert outcome.verification_state == "inconclusive"  # verify_tests="never" short-circuits
    assert len(_RecordingConversation.instances) == 1
    kwargs = _RecordingConversation.instances[0].kwargs
    assert kwargs["max_iteration_per_run"] == 17
    assert kwargs["agent"] == "fake-agent"
    assert kwargs["workspace"] == "fake-workspace"


def test_no_progress_check_does_not_apply_after_successful_recovery(monkeypatch):
    # A retry that fixes the problem must never be second-guessed by the
    # no-progress check — "verified" isn't in _RETRIABLE_STATES, so the
    # signature comparison shouldn't even run.
    results = iter(
        [
            _run(_check(status="failed", exit_code=1, summary="1 failed", output="boom")),
            _run(_check(status="passed", summary="3 passed")),
        ]
    )
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: next(results))

    conversation = _FakeConversation()
    emitted = []
    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=2),
        emitted.append,
        "do the thing",
    )

    assert outcome.verification_state == "verified"
    assert emitted == []
