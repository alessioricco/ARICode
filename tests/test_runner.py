"""End-to-end smoke test for the runner, plus unit tests for the harness-side
verification loop (`_verify_and_report`) — those need no LLM/network, only a
fake `Conversation`-like object and a monkeypatched `_run_verification`.

The e2e test skips cleanly when no LLM key is configured, so CI passes
without secrets. When a key is present, it runs one tiny, cheap task against
whichever provider/model `.env` currently points at (proving the harness is
provider-agnostic: this test never hardcodes a model).
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import ClassVar

import pytest
from openhands.sdk import ConversationExecutionStatus
from openhands.sdk.event import ObservationEvent
from openhands.tools.task_tracker import TaskTrackerObservation, TaskTrackerTool

from harness import runner
from harness.acceptance import AcceptanceCheck
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

    `initial_events`/`events_after_run` do the same for
    `state.events` (consulted by `_enforce_task_tracker_completion`, not by
    `_verify_and_report`) — `events_after_run` values are appended, one per
    `.run()` call, standing in for a task_tracker observation the agent
    produced during that retry.
    """

    def __init__(
        self,
        initial_status: ConversationExecutionStatus = ConversationExecutionStatus.FINISHED,
        statuses_after_run: list[ConversationExecutionStatus] | None = None,
        initial_events: list | None = None,
        events_after_run: list | None = None,
    ) -> None:
        self.sent_messages: list[str] = []
        self.run_calls = 0
        self.state = SimpleNamespace(
            execution_status=initial_status, events=list(initial_events or [])
        )
        self._statuses_after_run = list(statuses_after_run or [])
        self._events_after_run = list(events_after_run or [])

    def send_message(self, message: str) -> None:
        self.sent_messages.append(message)

    def run(self) -> None:
        self.run_calls += 1
        if self._statuses_after_run:
            self.state.execution_status = self._statuses_after_run.pop(0)
        if self._events_after_run:
            self.state.events.append(self._events_after_run.pop(0))


def _task_list_event(*items: dict) -> ObservationEvent:
    """A real `ObservationEvent` wrapping a real `TaskTrackerObservation` —
    constructed for real (not a bare mock) so `isinstance(event,
    ObservationEvent)` checks in `_task_tracker_snapshot` exercise the same
    type check production code relies on.
    """
    observation = TaskTrackerObservation.from_text(
        text="task list", command="plan", task_list=list(items)
    )
    return ObservationEvent(
        tool_name=TaskTrackerTool.name,
        tool_call_id="call_1",
        observation=observation,
        action_id="act_1",
    )


class _ConfirmConversation:
    """Stands in for `Conversation` in `_run_with_confirmation` tests.

    `statuses` is consumed one value per `.run()` call — the status that
    call leaves the conversation in. `_pending_actions` is monkeypatched
    separately in these tests rather than reconstructed from real events,
    since the SDK's own action/observation matching logic isn't what's
    under test here — only `_run_with_confirmation`'s approve/reject/
    no-handler control flow is.
    """

    def __init__(self, statuses: list[ConversationExecutionStatus]) -> None:
        # Unlike _FakeConversation, _run_with_confirmation always calls
        # .run() unconditionally as its first action (there's no
        # already-completed prior run to reflect), so every status in the
        # list is consumed by a .run() call — none are "pre-run" state.
        self._statuses = list(statuses)
        self.state = SimpleNamespace(execution_status=None)
        self.run_calls = 0
        self.rejected_reasons: list[str] = []
        self.confirmation_policies: list[object] = []

    def send_message(self, message: str) -> None:
        pass

    def run(self) -> None:
        self.run_calls += 1
        self.state.execution_status = self._statuses.pop(0)

    def reject_pending_actions(self, reason: str) -> None:
        self.rejected_reasons.append(reason)

    def set_confirmation_policy(self, policy: object) -> None:
        self.confirmation_policies.append(policy)


_FAKE_PENDING = [SimpleNamespace(tool_name="terminal", action="echo hi")]


# --- HARNESS_CONFIRM_MODE=always wiring (_run_with_confirmation) -----------


def test_run_with_confirmation_is_a_noop_when_confirm_mode_is_never():
    conversation = _ConfirmConversation([ConversationExecutionStatus.FINISHED])

    result = runner._run_with_confirmation(
        conversation, _cfg(confirm_mode="never"), lambda _m: None, on_confirm=None
    )

    assert result == "ok"
    assert conversation.run_calls == 1
    assert conversation.rejected_reasons == []


def test_run_with_confirmation_approves_and_continues(monkeypatch):
    monkeypatch.setattr(runner, "_pending_actions", lambda _conv: _FAKE_PENDING)
    conversation = _ConfirmConversation(
        [
            ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
            ConversationExecutionStatus.FINISHED,
        ]
    )
    seen = []

    def _approve(pending):
        seen.append(list(pending))
        return True

    result = runner._run_with_confirmation(
        conversation, _cfg(confirm_mode="always"), lambda _m: None, on_confirm=_approve
    )

    assert result == "ok"
    assert conversation.run_calls == 2
    assert seen == [_FAKE_PENDING]
    assert conversation.rejected_reasons == []


def test_run_with_confirmation_rejects_then_continues(monkeypatch):
    monkeypatch.setattr(runner, "_pending_actions", lambda _conv: _FAKE_PENDING)
    conversation = _ConfirmConversation(
        [
            ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
            ConversationExecutionStatus.FINISHED,
        ]
    )

    result = runner._run_with_confirmation(
        conversation, _cfg(confirm_mode="always"), lambda _m: None, on_confirm=lambda _p: False
    )

    assert result == "ok"
    assert conversation.run_calls == 2
    assert conversation.rejected_reasons == ["Rejected by the user via harness confirm-mode."]


def test_run_with_confirmation_stops_and_rejects_once_when_no_handler(monkeypatch):
    # No on_confirm callback available (e.g. server mode) — must not
    # silently approve (defeats the safety gate) or loop forever with
    # nobody able to answer.
    monkeypatch.setattr(runner, "_pending_actions", lambda _conv: _FAKE_PENDING)
    conversation = _ConfirmConversation([ConversationExecutionStatus.WAITING_FOR_CONFIRMATION])
    emitted = []

    result = runner._run_with_confirmation(
        conversation, _cfg(confirm_mode="always"), emitted.append, on_confirm=None
    )

    assert result == "confirmation_required"
    assert conversation.run_calls == 1  # no further run() calls after the unanswered pause
    assert len(conversation.rejected_reasons) == 1
    assert len(emitted) == 1
    assert "no interactive approval" in emitted[0].content[0].text


def test_stream_task_sets_always_confirm_policy_when_confirm_mode_always(monkeypatch):
    conversation = _ConfirmConversation([ConversationExecutionStatus.FINISHED])
    monkeypatch.setattr(runner, "Conversation", lambda **_kwargs: conversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: nullcontext("fake-workspace"))
    monkeypatch.setattr(runner, "_enforce_task_tracker_completion", lambda *a, **kw: None)
    monkeypatch.setattr(
        runner,
        "_verify_and_report",
        lambda *a, **kw: runner.TaskOutcome(
            verification_state="inconclusive",
            completion_contract=runner.CompletionContract(
                goal="", acceptance_criteria=[], verification_checks=[], limitations=[]
            ),
        ),
    )

    runner.stream_task("do the thing", cfg=_cfg(confirm_mode="always"))

    assert len(conversation.confirmation_policies) == 1
    from openhands.sdk.security.confirmation_policy import AlwaysConfirm

    assert isinstance(conversation.confirmation_policies[0], AlwaysConfirm)


def test_stream_task_short_circuits_when_confirmation_required_with_no_handler(monkeypatch):
    monkeypatch.setattr(runner, "_pending_actions", lambda _conv: _FAKE_PENDING)
    conversation = _ConfirmConversation([ConversationExecutionStatus.WAITING_FOR_CONFIRMATION])
    monkeypatch.setattr(runner, "Conversation", lambda **_kwargs: conversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: nullcontext("fake-workspace"))
    tracker_calls = {"count": 0}
    monkeypatch.setattr(
        runner,
        "_enforce_task_tracker_completion",
        lambda *a, **kw: tracker_calls.__setitem__("count", tracker_calls["count"] + 1),
    )

    outcome = runner.stream_task("do the thing", cfg=_cfg(confirm_mode="always"))

    assert outcome.verification_state == "confirmation_required"
    assert tracker_calls["count"] == 0  # never reached task_tracker enforcement


# --- HARNESS_MAX_TASK_SECONDS: shared task-level budget ---------------------
#
# Regression coverage for the gap logged in ROADMAP.md: HARNESS_MAX_ITERATIONS
# only bounds a single conversation.run() call, but runner.py can call
# .run() up to three times per task (the initial run, then up to
# max_verify_retries more in each of the two retry loops), each getting its
# own fresh iteration budget from the SDK — so a task could spend roughly
# max_iterations * (1 + 2 * max_verify_retries) iterations, well beyond what
# the configured cap suggests. `deadline` (an absolute time.monotonic()
# timestamp) is checked before every conversation.run() call these tests
# exercise.


def test_run_with_confirmation_reports_budget_exhausted_before_any_run_call():
    conversation = _ConfirmConversation([ConversationExecutionStatus.FINISHED])

    result = runner._run_with_confirmation(
        conversation, _cfg(confirm_mode="never"), lambda _m: None, on_confirm=None, deadline=-1.0
    )

    assert result == "budget_exhausted"
    assert conversation.run_calls == 0  # never even attempted the run


def test_run_with_confirmation_reports_budget_exhausted_mid_confirm_loop(monkeypatch):
    # The deadline passes *between* two confirm-mode round-trips, not before
    # the very first .run() call — must still be caught, not just checked
    # once at the top. First time.monotonic() call is the pre-run check
    # (still within budget); the second is inside the confirm loop, after
    # the initial run already paused for confirmation (budget now exhausted).
    monkeypatch.setattr(runner, "_pending_actions", lambda _conv: _FAKE_PENDING)
    conversation = _ConfirmConversation([ConversationExecutionStatus.WAITING_FOR_CONFIRMATION])
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    result = runner._run_with_confirmation(
        conversation,
        _cfg(confirm_mode="always"),
        lambda _m: None,
        on_confirm=lambda _p: True,
        deadline=5.0,
    )

    assert result == "budget_exhausted"
    assert conversation.run_calls == 1  # the initial run only — no approved retry


def test_verify_and_report_reports_budget_exhausted_at_entry(monkeypatch):
    monkeypatch.setattr(runner, "_run_verification", lambda _dir: _run(_check(summary="3 passed")))
    conversation = _FakeConversation()

    outcome = runner._verify_and_report(
        conversation, _cfg(verify_tests="always"), lambda _m: None, "do the thing", deadline=-1.0
    )

    assert outcome.verification_state == "budget_exhausted"
    assert conversation.run_calls == 0


def test_verify_and_report_reports_budget_exhausted_mid_retry(monkeypatch):
    # deadline is still valid at entry (the first time.monotonic() call
    # returns a value below it) but exhausted by the time the retry loop's
    # own _run_with_confirmation call checks again (the second call).
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: _run(_check(status="failed", exit_code=1, summary="1 failed")),
    )
    conversation = _FakeConversation()
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    outcome = runner._verify_and_report(
        conversation,
        _cfg(verify_tests="always", max_verify_retries=5),
        lambda _m: None,
        "do the thing",
        on_confirm=None,
        deadline=50.0,
    )

    assert outcome.verification_state == "budget_exhausted"
    assert outcome.retries_used == 1
    assert conversation.run_calls == 0


def test_enforce_task_tracker_completion_reports_budget_exhausted_at_entry():
    conversation = _FakeConversation(
        initial_events=[_task_list_event({"title": "B", "status": "todo"})]
    )

    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), lambda _m: None, "do the thing", deadline=-1.0
    )

    assert outcome is not None
    assert outcome.verification_state == "budget_exhausted"
    assert conversation.run_calls == 0


def test_enforce_task_tracker_completion_reports_budget_exhausted_mid_retry(monkeypatch):
    # Same shape as _verify_and_report's equivalent test: deadline is still
    # valid at entry, exhausted by the time the retry loop's own
    # _run_with_confirmation call checks again.
    conversation = _FakeConversation(
        initial_events=[_task_list_event({"title": "B", "status": "todo"})]
    )
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    outcome = runner._enforce_task_tracker_completion(
        conversation,
        _cfg(max_verify_retries=5),
        lambda _m: None,
        "do the thing",
        on_confirm=None,
        deadline=50.0,
    )

    assert outcome is not None
    assert outcome.verification_state == "budget_exhausted"
    assert outcome.retries_used == 1
    assert conversation.run_calls == 0


def test_stream_task_short_circuits_when_task_budget_already_exhausted(monkeypatch):
    # First time.monotonic() call computes `deadline = now + max_task_seconds`
    # inside stream_task; the second is _run_with_confirmation's own check,
    # simulating that max_task_seconds' worth of wall-clock time has already
    # passed between the two (a tiny max_task_seconds makes this realistic,
    # but the clock is mocked so the test doesn't need to actually wait).
    conversation = _RecordingConversation()
    monkeypatch.setattr(runner, "Conversation", lambda **_kwargs: conversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: nullcontext("fake-workspace"))
    tracker_calls = {"count": 0}
    monkeypatch.setattr(
        runner,
        "_enforce_task_tracker_completion",
        lambda *a, **kw: tracker_calls.__setitem__("count", tracker_calls["count"] + 1),
    )
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    outcome = runner.stream_task("do the thing", cfg=_cfg(max_task_seconds=1, verify_tests="never"))

    assert outcome.verification_state == "budget_exhausted"
    assert tracker_calls["count"] == 0  # never reached task_tracker enforcement


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


# --- task_tracker completion enforcement ------------------------------------
#
# Regression coverage for the "agent declares done with incomplete
# task_tracker items" failure mode logged in ROADMAP.md: prior to this,
# only a prompt-level mitigation (_AUTONOMOUS_SUFFIX) existed, which is soft
# and unverifiable by a test. Live-confirmed the exact failure this guards
# against: given "create two tasks, mark one done, leave the other todo,
# then finish", the agent did exactly that and called finish anyway — see
# ROADMAP.md's decisions log for the transcript.


def test_task_tracker_never_used_is_not_enforced():
    # The tool's own guidance says trivial tasks don't need it — no
    # task_tracker observation at all must not be treated as a violation.
    conversation = _FakeConversation()
    emitted = []
    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), emitted.append, "do the thing"
    )

    assert outcome is None
    assert conversation.run_calls == 0
    assert emitted == []


def test_task_tracker_all_done_is_not_enforced():
    conversation = _FakeConversation(
        initial_events=[_task_list_event({"title": "A", "status": "done"})]
    )
    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), lambda _m: None, "do the thing"
    )

    assert outcome is None
    assert conversation.run_calls == 0


def test_task_tracker_pending_item_triggers_followup_then_recovers():
    conversation = _FakeConversation(
        initial_events=[
            _task_list_event({"title": "A", "status": "done"}, {"title": "B", "status": "todo"})
        ],
        events_after_run=[
            _task_list_event({"title": "A", "status": "done"}, {"title": "B", "status": "done"})
        ],
    )
    emitted = []
    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 1
    assert len(conversation.sent_messages) == 1
    assert "task_tracker" in conversation.sent_messages[0]
    assert "B" in conversation.sent_messages[0]
    assert emitted == []  # recovered — no give-up notice
    assert outcome is None  # complete now — stream_task proceeds to _verify_and_report


def test_task_tracker_exhausts_retries_and_emits_giveup_notice():
    conversation = _FakeConversation(
        initial_events=[_task_list_event({"title": "B", "status": "todo"})],
        events_after_run=[
            _task_list_event({"title": "B", "status": "in_progress"}),
            _task_list_event({"title": "B", "status": "in_progress"}),
        ],
    )
    emitted = []
    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 2
    assert outcome is not None
    assert outcome.verification_state == "incomplete"
    assert outcome.retries_used == 2
    assert outcome.success is False
    assert len(emitted) == 1
    assert "task_tracker" in emitted[0].content[0].text
    assert "B" in emitted[0].content[0].text
    assert any("B" in note for note in outcome.completion_contract.limitations)
    assert outcome.completion_contract.verification_checks == []


def test_task_tracker_stuck_before_check_is_reported_as_stuck():
    conversation = _FakeConversation(
        initial_status=ConversationExecutionStatus.STUCK,
        initial_events=[_task_list_event({"title": "B", "status": "todo"})],
    )
    emitted = []
    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 0  # never even tried to follow up on a stuck run
    assert outcome is not None
    assert outcome.verification_state == "stuck"
    assert len(emitted) == 1


def test_task_tracker_gets_stuck_mid_retry():
    conversation = _FakeConversation(
        initial_events=[_task_list_event({"title": "B", "status": "todo"})],
        statuses_after_run=[ConversationExecutionStatus.STUCK],
    )
    emitted = []
    outcome = runner._enforce_task_tracker_completion(
        conversation, _cfg(max_verify_retries=2), emitted.append, "do the thing"
    )

    assert conversation.run_calls == 1  # got stuck on the first retry, no second attempt
    assert outcome is not None
    assert outcome.verification_state == "stuck"
    assert outcome.retries_used == 1


def test_task_tracker_short_circuits_project_verification_when_still_incomplete(monkeypatch):
    # Integration check for the full stream_task wiring, not just the unit
    # function in isolation: an incomplete task_tracker list must prevent
    # project verification from ever running under a false premise of
    # completeness.
    class _Conversation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.run_calls = 0
            self.state = SimpleNamespace(
                execution_status=ConversationExecutionStatus.FINISHED,
                events=[_task_list_event({"title": "B", "status": "todo"})],
            )

        def send_message(self, message: str) -> None:
            pass

        def run(self) -> None:
            self.run_calls += 1  # stays incomplete across every retry

    verification_calls = {"count": 0}
    monkeypatch.setattr(
        runner,
        "_run_verification",
        lambda _dir: (
            verification_calls.__setitem__("count", verification_calls["count"] + 1)
            or _run(_check(summary="3 passed"))
        ),
    )
    monkeypatch.setattr(runner, "Conversation", _Conversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: nullcontext("fake-workspace"))

    outcome = runner.stream_task(
        "do the thing", cfg=_cfg(verify_tests="always", max_verify_retries=1)
    )

    assert outcome.verification_state == "incomplete"
    assert verification_calls["count"] == 0  # project verification never reached


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
        self.state = SimpleNamespace(
            execution_status=ConversationExecutionStatus.FINISHED, events=[]
        )
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


# --- _apply_acceptance_checks: caller-supplied acceptance criteria ---------


def _outcome(verification_state: str = "verified") -> runner.TaskOutcome:
    contract = runner.CompletionContract(
        goal="do the thing", acceptance_criteria=[], verification_checks=[], limitations=[]
    )
    return runner.TaskOutcome(verification_state=verification_state, completion_contract=contract)


def test_apply_acceptance_checks_is_a_noop_when_none_given(tmp_path):
    outcome = _outcome()

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), lambda _m: None, None
    )

    assert result is outcome


def test_apply_acceptance_checks_is_a_noop_when_empty_list(tmp_path):
    outcome = _outcome()

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), lambda _m: None, []
    )

    assert result is outcome


def test_apply_acceptance_checks_all_pass_keeps_verified(tmp_path):
    (tmp_path / "README.md").write_text("# Usage\n...")
    outcome = _outcome("verified")
    checks = [AcceptanceCheck(kind="file_exists", path="README.md")]

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), lambda _m: None, checks
    )

    assert result.verification_state == "verified"
    assert len(result.acceptance_results) == 1
    assert result.acceptance_results[0].passed is True
    assert result.completion_contract.limitations == []
    assert any("README.md" in c for c in result.completion_contract.acceptance_criteria)


def test_apply_acceptance_checks_required_failure_downgrades_verified(tmp_path):
    outcome = _outcome("verified")
    checks = [AcceptanceCheck(kind="file_exists", path="OUTPUT.txt", description="the output file")]
    emitted = []

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), emitted.append, checks
    )

    assert result.verification_state == "acceptance_failed"
    assert result.acceptance_results[0].passed is False
    assert any("the output file" in note for note in result.completion_contract.limitations)
    assert len(emitted) == 1
    assert "acceptance check" in emitted[0].content[0].text.lower()


def test_apply_acceptance_checks_optional_failure_does_not_downgrade(tmp_path):
    outcome = _outcome("verified")
    checks = [AcceptanceCheck(kind="file_exists", path="OUTPUT.txt", required=False)]
    emitted = []

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), emitted.append, checks
    )

    assert result.verification_state == "verified"  # unaffected — the check wasn't required
    assert result.acceptance_results[0].passed is False
    assert any(
        "Optional acceptance check failed" in note
        for note in result.completion_contract.limitations
    )
    assert emitted == []  # no give-up notice — nothing was blocked


def test_apply_acceptance_checks_does_not_override_a_non_verified_state(tmp_path):
    # A run that already ended in some other failure state (retry_exhausted,
    # stuck, etc.) has nothing left to "block" — acceptance results are
    # still attached for visibility, but the original state is preserved.
    outcome = _outcome("retry_exhausted")
    checks = [AcceptanceCheck(kind="file_exists", path="OUTPUT.txt")]

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), lambda _m: None, checks
    )

    assert result.verification_state == "retry_exhausted"
    assert result.acceptance_results[0].passed is False


def test_apply_acceptance_checks_rejects_a_path_escape_without_crashing(tmp_path):
    # evaluate_acceptance_check turns a rejected path into a failed result,
    # not an exception — _apply_acceptance_checks must not need to know that.
    outcome = _outcome("verified")
    checks = [AcceptanceCheck(kind="file_exists", path="/etc/passwd", required=True)]

    result = runner._apply_acceptance_checks(
        outcome, _cfg(workspace=str(tmp_path)), lambda _m: None, checks
    )

    assert result.verification_state == "acceptance_failed"
    assert result.acceptance_results[0].passed is False
    assert "absolute" in result.acceptance_results[0].detail


def test_stream_task_applies_acceptance_checks_end_to_end(monkeypatch, tmp_path):
    (tmp_path / "OUTPUT.txt").write_text("done")
    conversation = _RecordingConversation()
    monkeypatch.setattr(runner, "Conversation", lambda **_kwargs: conversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: nullcontext(str(tmp_path)))
    monkeypatch.setattr(runner, "_enforce_task_tracker_completion", lambda *a, **kw: None)
    monkeypatch.setattr(
        runner,
        "_verify_and_report",
        lambda *a, **kw: runner.TaskOutcome(
            verification_state="verified",
            completion_contract=runner.CompletionContract(
                goal="", acceptance_criteria=[], verification_checks=[], limitations=[]
            ),
        ),
    )

    outcome = runner.stream_task(
        "do the thing",
        cfg=_cfg(workspace=str(tmp_path)),
        acceptance_checks=[AcceptanceCheck(kind="file_exists", path="OUTPUT.txt")],
    )

    assert outcome.verification_state == "verified"
    assert len(outcome.acceptance_results) == 1
    assert outcome.acceptance_results[0].passed is True


def test_stream_task_acceptance_check_failure_downgrades_a_real_verified_run(monkeypatch, tmp_path):
    conversation = _RecordingConversation()
    monkeypatch.setattr(runner, "Conversation", lambda **_kwargs: conversation)
    monkeypatch.setattr(runner, "build_agent", lambda cfg: "fake-agent")
    monkeypatch.setattr(runner, "build_workspace", lambda cfg: nullcontext(str(tmp_path)))
    monkeypatch.setattr(runner, "_enforce_task_tracker_completion", lambda *a, **kw: None)
    monkeypatch.setattr(
        runner,
        "_verify_and_report",
        lambda *a, **kw: runner.TaskOutcome(
            verification_state="verified",
            completion_contract=runner.CompletionContract(
                goal="", acceptance_criteria=[], verification_checks=[], limitations=[]
            ),
        ),
    )

    outcome = runner.stream_task(
        "do the thing",
        cfg=_cfg(workspace=str(tmp_path)),
        acceptance_checks=[AcceptanceCheck(kind="file_exists", path="OUTPUT.txt")],
    )

    assert outcome.verification_state == "acceptance_failed"
