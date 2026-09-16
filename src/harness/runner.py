"""Runs a task end to end and returns/streams the conversation's messages.

Result is captured via an event callback — there is no `conversation.result`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from openhands.sdk import (
    Conversation,
    ConversationExecutionStatus,
    Event,
    LLMConvertibleEvent,
    Message,
    TextContent,
)

from .agent import build_agent
from .config import Config, load_config
from .custom_tools.run_tests_tool import CheckOutcome, VerificationRun, run_full_verification
from .workspace import build_workspace

# The seven terminal states a task run can end in. Exactly these because
# they're the ones a caller needs to tell apart to know whether to trust a
# "done" claim: real, confirmed success; no evidence either way; a confirmed
# problem that automated retries couldn't fix; a fix attempt that produced
# no observable change at all (see `_failure_signature` below — a distinct
# problem from "still failing differently"); a check that kept exceeding its
# timeout even after retries (a distinct failure mode from a genuine failure
# — usually a hang/infinite loop, not a wrong answer); and a run that never
# reached a coherent finish at all. "failed" itself is never a *final* value
# here — it's the transient signal inside the retry loop between "a check
# just failed" and "was it fixed, or did retries run out" (see
# `_verify_and_report`); it's listed because `VerificationRun.state`
# (run_tests_tool.py) uses the same vocabulary and a caller may inspect a
# `TaskOutcome.checks` mid-analysis. See MANUAL.md "Test verification" and
# ROADMAP.md's decisions log.
VERIFICATION_STATES = (
    "verified",
    "failed",
    "inconclusive",
    "retry_exhausted",
    "no_progress",
    "timed_out",
    "stuck",
)

# VerificationRun states that mean "something needs fixing, worth retrying" —
# as opposed to "inconclusive" (nothing to retry toward) or "verified".
_RETRIABLE_STATES = ("failed", "timed_out")

# execution_status values that mean the conversation did not end via a normal
# finish — retrying or verifying against whatever state the workspace is in
# would be building on top of a run the agent itself never completed coherently.
_ABORTED_STATUSES = (ConversationExecutionStatus.STUCK, ConversationExecutionStatus.ERROR)

# Wall-clock durations a test/build tool prints in its own summary line
# (pytest's "in 3.85s", etc.) — the one kind of noise expected to differ
# between two otherwise-identical runs. Stripped only from the *comparison*
# signature below, never from the real output shown to the agent.
_DURATION_NOISE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:s|ms|seconds?|milliseconds?)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class CompletionContract:
    """A structured, harness-computed statement of what a task run was
    actually judged against — deliberately not another LLM call or a bigger
    prompt (see ROADMAP.md's decisions log): the goal is a record attached to
    the result, not more instructions fed back into the agent.
    """

    goal: str
    acceptance_criteria: list[str]
    verification_checks: list[str]
    limitations: list[str]


@dataclass(frozen=True)
class TaskOutcome:
    """The terminal, caller-facing verdict for one task run.

    `stream_task` always returns one of these now (previously returned
    `None`); `run_task` attaches it to its `TaskResult` list subclass as
    `.outcome`, so every existing caller that only ever used the return
    value as `list[Message]` (`messages[-1]`, `len(messages)`, `if
    messages:`, iteration) keeps working unchanged.
    """

    verification_state: str  # one of VERIFICATION_STATES
    completion_contract: CompletionContract
    checks: tuple[CheckOutcome, ...] = ()
    retries_used: int = 0

    @property
    def success(self) -> bool:
        return self.verification_state == "verified"


class TaskResult(list):
    """`run_task`'s return value: behaves exactly like the `list[Message]`
    it has always returned, plus `.outcome` (a `TaskOutcome`) for callers
    that want the structured verdict instead of re-deriving it from message
    text.
    """

    def __init__(self, messages, outcome: TaskOutcome) -> None:
        super().__init__(messages)
        self.outcome = outcome


def _run_verification(working_dir: str) -> VerificationRun:
    """Run the project's full verification directly — no LLM/tool-call round
    trip. A verification-step glitch (a check hangs past its own timeout, or
    something else genuinely unexpected) must not abort an otherwise-
    successful task, so it's treated as inconclusive (no checks), not
    re-raised.
    """
    try:
        return run_full_verification(working_dir)
    except Exception:  # noqa: BLE001 - a verification glitch must not abort the task
        return VerificationRun(checks=[])


def _is_clean_no_tests_collected(run: VerificationRun) -> bool:
    """True only for the specific, well-defined "pytest ran cleanly and
    collected zero tests" signal — the one case req'd to keep this feature's
    original silent, non-blocking behavior (see MANUAL.md "Test
    verification"). Any other inconclusive outcome (npm missing, a
    configured lint/typecheck tool not installed, an unknown project type,
    ...) is a genuine "we don't know" and must be visible, not silently
    swept in with this one.
    """
    return (
        len(run.checks) == 1
        and run.checks[0].name == "pytest"
        and run.checks[0].status == "skipped"
    )


def _failure_signature(run: VerificationRun) -> tuple:
    """A signature of a `VerificationRun`'s failing/timed-out checks, stable
    across cosmetic differences (wall-clock timing) but sensitive to any
    real change in what actually failed.

    Used to catch a distinct problem from "still failing": a fix attempt
    that provably changed *nothing observable* — the exact same check,
    same exit code, same output — even though a real code edit and a real
    verification run happened in between. Confirmed live: an agent edited
    `move_disk`'s comparison operator (`<=` to `<`) twice across two retries
    to "fix" a failing test, but the edit was a no-op for that failure (the
    code path the test actually exercised was gated by a different branch
    entirely) — pytest's output was identical, digit-for-digit, before and
    after both edits except for its own run-duration footer. Two identical
    fix-then-still-broken cycles in a row is a much stronger, cheaper, and
    more reliable signal to stop on than trying to judge whether the
    agent's own prose explanation still makes sense (see ROADMAP.md's
    decisions log for why a text-coherence heuristic was rejected in favor
    of this one).
    """
    problems = run.failing + run.timed_out_checks
    return tuple(
        (c.name, c.status, c.exit_code, _DURATION_NOISE_RE.sub("", c.output)) for c in problems
    )


# If a collection-time error is a syntax/indentation problem, the fix is
# almost always a one- or two-line structural correction at an exact
# location pytest's own traceback already names — not a reason to rewrite
# the file. Confirmed live this needed saying explicitly: across repeated
# verification retries, the agent kept doing full-file rewrites to "fix" an
# IndentationError, each one leaving a new orphaned fragment (a stray
# top-level `return` between two methods) that produced a *different*
# IndentationError on the next check — file size grew, the bug moved, it
# never got fixed. Only fires for this specific error class; a logic-level
# test failure doesn't get this nudge since a broader edit may genuinely be
# the right fix there.
_SYNTAX_ERROR_MARKERS = ("IndentationError", "SyntaxError", "TabError")

# The JS/TS analogue of _SYNTAX_ERROR_MARKERS above, for an `npm run build`
# (or `npm test`) failure — confirmed live against the actual error class
# this generalization was built for: a Vite/oxc parse error ("Adjacent JSX
# elements must be wrapped in an enclosing tag") from unclosed/sibling JSX
# in App.tsx.
_JS_SYNTAX_ERROR_MARKERS = ("PARSE_ERROR", "SyntaxError", "Unexpected token")


def _check_followup(check: CheckOutcome) -> str:
    if check.status == "timed_out":
        guidance = (
            "This check exceeded its timeout and was killed — most likely "
            "an infinite loop, an unbounded wait, or a hang in the code you "
            "wrote, not a slow environment. Find and fix whatever is "
            "hanging; do not simply increase a timeout value (if one "
            "exists) as a substitute for fixing the actual cause."
        )
        return f"### {check.name} (`{check.command}`) — {check.summary}\n\n{check.output}\n\n{guidance}"
    guidance = (
        "Fix the underlying issue yourself — do not weaken, skip, delete, or "
        "disable this check to make it pass — and only stop once it "
        "actually succeeds. Make a minimal, targeted edit at the exact "
        "location the output points to rather than rewriting the whole file "
        "from scratch — a full rewrite risks losing or duplicating code you "
        "already had correct and introducing a new bug elsewhere."
    )
    if any(marker in check.output for marker in _SYNTAX_ERROR_MARKERS):
        guidance += (
            " This specific failure is a Python syntax/indentation error, "
            "not a logic bug: open the file, view the exact line(s) the "
            "traceback names, and fix only that structural issue "
            "(indentation level, a misplaced statement, a missing block) — "
            "verify the file parses (e.g. `python -m py_compile "
            "<file>`) before moving on."
        )
    elif any(marker in check.output for marker in _JS_SYNTAX_ERROR_MARKERS):
        guidance += (
            " This is a JavaScript/TypeScript syntax or parse error: open "
            "the exact file and line the error names and fix only that "
            "structural issue (e.g. wrap adjacent JSX elements in a single "
            "enclosing tag or fragment `<>...</>`)."
        )
    return f"### {check.name} (`{check.command}`) — {check.summary}\n\n{check.output}\n\n{guidance}"


def _verification_followup(run: VerificationRun) -> str:
    problem_checks = run.failing + run.timed_out_checks
    sections = "\n\n---\n\n".join(_check_followup(c) for c in problem_checks)
    return (
        "Automated verification ran the project's checks after you "
        "finished, and the following are still failing:\n\n"
        f"{sections}\n\n"
        "Fix every issue above before finishing again — a `finish` call is "
        "not appropriate while any of them are still failing or hanging."
    )


def _build_completion_contract(
    task: str, run: VerificationRun | None, *, skipped: bool, aborted: bool
) -> CompletionContract:
    checks = run.commands if run is not None else []
    limitations: list[str] = []
    if aborted:
        limitations.append(
            "The agent's run did not reach a normal finish (stuck or errored "
            "state) before verification could run; its own completion claim, "
            "if any, was not verified."
        )
    if skipped:
        limitations.append(
            "Automated verification was skipped (HARNESS_VERIFY_TESTS=never); "
            "completion was not independently checked."
        )
    if run is not None:
        limitations.extend(run.limitation_notes)
    if not aborted and not skipped and not checks:
        limitations.append(
            "No automated check could be run for this project (no known "
            "project type or configured check was detected)."
        )
    acceptance_criteria = ["The task described in the original request is implemented."]
    acceptance_criteria.extend(f"`{cmd}` completes successfully." for cmd in checks)
    return CompletionContract(
        goal=task,
        acceptance_criteria=acceptance_criteria,
        verification_checks=checks,
        limitations=limitations,
    )


def _emit_notice(emit: Callable[[Message], None], text: str) -> None:
    emit(Message(role="assistant", content=[TextContent(text=text)]))


def _verify_and_report(
    conversation: Conversation, cfg: Config, emit: Callable[[Message], None], task: str
) -> TaskOutcome:
    """Post-hoc safety net for agent.py's `_VERIFY_BEFORE_FINISH_SUFFIX`: run
    the project's own checks ourselves instead of trusting the agent's
    self-report, and never report a failed or unverified run as success.

    Confirmed live this matters, not just in theory — see ROADMAP.md's
    decisions log for two independent, concrete false-pass reproductions
    (a Python `IndentationError` the agent's own summary misdiagnosed, and a
    JSX parse error a pytest-only check couldn't even see). Bounded by
    `cfg.max_verify_retries` so a project whose checks are simply wrong, or a
    bug the model can't fix, doesn't loop forever.
    """
    status = conversation.state.execution_status
    if status in _ABORTED_STATUSES:
        contract = _build_completion_contract(task, None, skipped=False, aborted=True)
        _emit_notice(
            emit,
            "Harness verification: the agent's run ended in a "
            f"'{status.value}' state before it reached a normal finish, so "
            "its own claims — if it made any — were not verified. This task "
            "needs manual attention.",
        )
        return TaskOutcome(verification_state="stuck", completion_contract=contract)

    if cfg.verify_tests != "always":
        contract = _build_completion_contract(task, None, skipped=True, aborted=False)
        return TaskOutcome(verification_state="inconclusive", completion_contract=contract)

    working_dir = os.path.abspath(cfg.workspace)
    attempts_left = cfg.max_verify_retries
    retries_used = 0
    run = _run_verification(working_dir)

    while run.state in _RETRIABLE_STATES:
        if attempts_left <= 0:
            contract = _build_completion_contract(task, run, skipped=False, aborted=False)
            # "retry_exhausted" for a genuine failure (real output the agent
            # could act on), but a persistent timeout is kept as its own
            # terminal state — a hang that never resolves across retries is
            # a different problem from a wrong answer, worth telling apart.
            final_state = "retry_exhausted" if run.state == "failed" else "timed_out"
            problem_text = "still failing" if run.state == "failed" else "still timing out"
            problems = "; ".join(c.summary for c in run.failing + run.timed_out_checks)
            _emit_notice(
                emit,
                f"Harness verification: the project's checks are {problem_text} "
                f"after {cfg.max_verify_retries} automated fix "
                f"attempt(s) ({problems}). Giving up — this project needs "
                "manual attention.",
            )
            return TaskOutcome(
                verification_state=final_state,
                completion_contract=contract,
                checks=tuple(run.checks),
                retries_used=retries_used,
            )
        previous_signature = _failure_signature(run)
        attempts_left -= 1
        retries_used += 1
        conversation.send_message(_verification_followup(run))
        conversation.run()

        retry_status = conversation.state.execution_status
        if retry_status in _ABORTED_STATUSES:
            contract = _build_completion_contract(task, run, skipped=False, aborted=True)
            _emit_notice(
                emit,
                "Harness verification: the agent got stuck "
                f"(status '{retry_status.value}') while retrying, after "
                f"{retries_used} fix attempt(s). This task needs manual "
                "attention.",
            )
            return TaskOutcome(
                verification_state="stuck",
                completion_contract=contract,
                checks=tuple(run.checks),
                retries_used=retries_used,
            )
        run = _run_verification(working_dir)

        # A fix attempt that changes literally nothing observable is a
        # different, cheaper-to-detect problem than "still failing" — stop
        # now rather than spending the rest of the retry budget on attempts
        # unlikely to help. See `_failure_signature`'s docstring for the
        # live case this catches.
        if run.state in _RETRIABLE_STATES and _failure_signature(run) == previous_signature:
            contract = _build_completion_contract(task, run, skipped=False, aborted=False)
            problems = "; ".join(c.summary for c in run.failing + run.timed_out_checks)
            _emit_notice(
                emit,
                "Harness verification: the fix attempt just made produced "
                f"no observable change — the same check ({problems}) is "
                "still failing with identical output (ignoring timing). "
                "Stopping early rather than spending the rest of the retry "
                "budget on attempts that already provably didn't help — "
                "this project needs manual attention.",
            )
            return TaskOutcome(
                verification_state="no_progress",
                completion_contract=contract,
                checks=tuple(run.checks),
                retries_used=retries_used,
            )

    contract = _build_completion_contract(task, run, skipped=False, aborted=False)
    if run.state == "inconclusive" and not _is_clean_no_tests_collected(run):
        summaries = "; ".join(c.summary for c in run.checks) or "no known project type detected"
        _emit_notice(
            emit,
            "Harness verification: automated checks found nothing runnable "
            f"to confirm this project actually works ({summaries}). The "
            "agent's own completion claim was not independently verified.",
        )
    return TaskOutcome(
        verification_state=run.state,
        completion_contract=contract,
        checks=tuple(run.checks),
        retries_used=retries_used,
    )


def stream_task(
    task: str, cfg: Config | None = None, on_message: Callable[[Message], None] | None = None
) -> TaskOutcome:
    """Run a task, invoking `on_message` with each message as it's produced,
    and return the terminal `TaskOutcome` once verification has settled.

    Shared by `run_task` (collects into a list) and the server's WebSocket
    endpoint (pushes each message to the client as it arrives).
    """
    if cfg is None:
        cfg = load_config()
    emit = on_message or (lambda _msg: None)

    def on_event(event: Event) -> None:
        if isinstance(event, LLMConvertibleEvent):
            emit(event.to_llm_message())

    with build_workspace(cfg) as workspace:
        conversation = Conversation(
            agent=build_agent(cfg),
            callbacks=[on_event],
            workspace=workspace,
            # Previously unset (SDK default 500), so HARNESS_MAX_ITERATIONS
            # was parsed/validated but silently had no effect — found while
            # bounding the verify-and-retry loop above, which reuses the same
            # conversation.run() and needed this to actually mean something.
            max_iteration_per_run=cfg.max_iterations,
        )
        conversation.send_message(task)
        conversation.run()
        return _verify_and_report(conversation, cfg, emit, task)


def run_task(task: str, cfg: Config | None = None) -> TaskResult:
    messages: list = []
    outcome = stream_task(task, cfg=cfg, on_message=messages.append)
    return TaskResult(messages, outcome)  # last message is the final assistant output
