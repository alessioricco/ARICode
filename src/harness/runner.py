"""Runs a task end to end and returns/streams the conversation's messages.

Result is captured via an event callback — there is no `conversation.result`.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from openhands.sdk import (
    Conversation,
    ConversationExecutionStatus,
    Event,
    LLMConvertibleEvent,
    Message,
    TextContent,
)
from openhands.sdk.conversation import ConversationState
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm.exceptions.types import LLMError
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.tools.task_tracker import TaskTrackerTool

from .acceptance import AcceptanceCheck, AcceptanceCheckResult, evaluate_acceptance_checks
from .agent import build_agent
from .artifacts import write_run_artifacts
from .config import Config, load_config
from .custom_tools.run_tests_tool import CheckOutcome, VerificationRun, run_full_verification
from .model_catalog import classify_task, load_model_catalog, rank_candidates
from .model_selection import (
    ModelChain,
    ModelDecisionRecord,
    OnModelChoice,
    config_for_entry,
    llm_for_entry,
    write_model_decisions,
)
from .workspace import build_workspace

# The eleven terminal states a task run can end in. Exactly these because
# they're the ones a caller needs to tell apart to know whether to trust a
# "done" claim: real, confirmed success; no evidence either way; a confirmed
# problem that automated retries couldn't fix; a fix attempt that produced
# no observable change at all (see `_failure_signature` below — a distinct
# problem from "still failing differently"); a check that kept exceeding its
# timeout even after retries (a distinct failure mode from a genuine failure
# — usually a hang/infinite loop, not a wrong answer); the agent's own
# task_tracker list still showing unfinished work after automated follow-up
# (see `_enforce_task_tracker_completion` — distinct from every check above
# since it's caught before project verification even runs); an action that
# needed HARNESS_CONFIRM_MODE=always approval with no handler available to
# answer it (see `_run_with_confirmation` — caught before task_tracker or
# project verification, same reasoning as `incomplete`); the shared,
# task-level wall-clock budget (`HARNESS_MAX_TASK_SECONDS`) running out
# across the initial run and every retry phase combined — distinct from a
# single `conversation.run()` call hitting its own `max_iteration_per_run`
# (which resets on every call and so cannot bound a whole task on its own,
# see ROADMAP.md's decisions log); a caller-supplied, machine-checkable
# acceptance criterion (see `acceptance.py`) that was marked `required` and
# failed, downgrading what would otherwise have been `verified` — the only
# state a caller opts into by supplying `acceptance_checks` at all; and a
# run that never reached a coherent finish at all. "failed" itself is never
# a *final* value here — it's the transient signal inside the retry loop
# between "a check just failed" and "was it fixed, or did retries run out"
# (see `_verify_and_report`); it's listed because `VerificationRun.state`
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
    "incomplete",
    "confirmation_required",
    "budget_exhausted",
    "acceptance_failed",
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
    # Only non-empty when the caller supplied acceptance_checks — see
    # _apply_acceptance_checks. Evaluated regardless of verification_state
    # (even a stuck/incomplete run's workspace is still worth checking),
    # but only a required check's failure can downgrade verification_state
    # itself (from "verified" to "acceptance_failed").
    acceptance_results: tuple[AcceptanceCheckResult, ...] = ()
    # Only non-empty when cfg.model_selection == "auto" — see
    # model_selection.py's ModelChain. The same records are also written to
    # MODEL_DECISIONS.md in the project workspace (write_model_decisions).
    model_decisions: tuple[ModelDecisionRecord, ...] = ()

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


# A caller-supplied decision function for HARNESS_CONFIRM_MODE=always:
# given the agent's pending (not-yet-executed) actions, return True to
# approve them and let the run continue, False to reject them. `cli.py`
# supplies a real terminal-prompting implementation; server.py supplies
# none (no terminal to prompt at) — see `_run_with_confirmation` for what
# happens when no callback is given.
ConfirmCallback = Callable[[Sequence[ActionEvent]], bool]

# A caller-supplied handler for HARNESS_INTERACTIVE=yes: given the narrative
# text produced since the last checkpoint, return the human's reply (sent
# back to the agent) or a falsy value to end the interactive loop and let
# the task proceed to task_tracker/verification as usual. `cli.py` supplies
# a real terminal-prompting implementation; server.py supplies none (no
# terminal to prompt at, same reasoning as ConfirmCallback above) — see
# `stream_task`'s interactive loop for what happens when no callback is
# given (nothing: cfg.interactive alone has no effect without one).
OnAwaitingInput = Callable[[str], str | None]


def _narrative_text(messages: Sequence[Message]) -> str:
    """Join the human-readable text of every non-echo message — the same
    extraction rule as `server.py`'s `_narrative_texts`, kept as its own
    small copy here rather than imported (`server.py` depends on this
    module, not the other way around). See that function's docstring for
    why `role == "assistant"` is the wrong filter: an assistant turn that
    makes a tool call has empty `content` (the call lives in `tool_calls`);
    the actual reply/finish text arrives as a `tool`-role message instead.
    """
    texts = []
    for message in messages:
        if message.role in ("system", "user"):
            continue
        for content in message.content:
            text = getattr(content, "text", None)
            if text:
                texts.append(text)
    return "\n\n".join(texts)


_WAITING_FOR_CONFIRMATION = ConversationExecutionStatus.WAITING_FOR_CONFIRMATION


def _pending_actions(conversation: Conversation) -> list[ActionEvent]:
    return ConversationState.get_unmatched_actions(conversation.state.active_branch())


def _describe_pending_action(action_event: ActionEvent) -> str:
    if action_event.action is None:
        return action_event.tool_name
    return f"{action_event.tool_name}({action_event.action})"


def _confirmation_required_contract(task: str) -> CompletionContract:
    return CompletionContract(
        goal=task,
        acceptance_criteria=["The task described in the original request is implemented."],
        verification_checks=[],
        limitations=[
            (
                "The agent proposed an action that required confirmation "
                "(HARNESS_CONFIRM_MODE=always), but no interactive approval "
                "handler was available to answer it, so the run was stopped "
                "before task_tracker or project verification could run."
            )
        ],
    )


def _budget_exhausted_contract(task: str, cfg: Config) -> CompletionContract:
    return CompletionContract(
        goal=task,
        acceptance_criteria=["The task described in the original request is implemented."],
        verification_checks=[],
        limitations=[
            (
                "The task's shared wall-clock budget "
                f"(HARNESS_MAX_TASK_SECONDS={cfg.max_task_seconds}) ran out before "
                "the task reached a normal finish, so its own completion claim, if "
                "any, was not verified."
            )
        ],
    )


# _run_with_confirmation's outcome for one call: "ok" means the run reached
# a normal terminal status (finished, stuck, error — anything but an
# unanswered confirmation or an exhausted budget); the other two values are
# each their own terminal TaskOutcome.verification_state at the call site.
_RUN_OK = "ok"
_RUN_CONFIRMATION_REQUIRED = "confirmation_required"
_RUN_BUDGET_EXHAUSTED = "budget_exhausted"


def _outcome_for_run_result(
    result: str,
    task: str,
    cfg: Config,
    *,
    checks: tuple[CheckOutcome, ...] = (),
    retries_used: int = 0,
) -> TaskOutcome | None:
    """Build the terminal `TaskOutcome` for a non-`"ok"` `_run_with_confirmation`
    result (`"confirmation_required"` or `"budget_exhausted"`), or `None` if
    `result == "ok"` — the caller's signal to proceed normally instead of
    returning early. Shared by all three call sites so each doesn't repeat
    its own contract-selection branch.
    """
    if result == _RUN_OK:
        return None
    contract = (
        _confirmation_required_contract(task)
        if result == _RUN_CONFIRMATION_REQUIRED
        else _budget_exhausted_contract(task, cfg)
    )
    return TaskOutcome(
        verification_state=result,
        completion_contract=contract,
        checks=checks,
        retries_used=retries_used,
    )


def _run_conversation_once(
    conversation: Conversation,
    cfg: Config,
    emit: Callable[[Message], None],
    model_chain: ModelChain | None,
) -> None:
    """One `conversation.run()` call, transparently falling back through
    `model_chain` on a provider/API-level failure (auth error, rate limit,
    provider outage, context overflow — anything LiteLLM itself raises)
    until one succeeds or the chain is exhausted, in which case the last
    such error is re-raised. Any other exception (a real bug, not a
    provider problem) always propagates immediately, whether or not auto
    model selection is active — this is not a general retry-on-any-error
    mechanism.

    `LocalConversation.run()` catches every exception its run loop raises
    and re-raises it wrapped as `ConversationRunError` (confirmed by reading
    the SDK source, not assumed) — `.original_exception` is the real
    underlying exception, checked against
    `openhands.sdk.llm.exceptions.types.LLMError` here.

    Took two rounds of live verification to land on the right type, not
    assumed either time: a first guess (`litellm.exceptions.APIError`) was
    wrong because every concrete LiteLLM exception actually subclasses the
    *same-named* class from the `openai` package, not `litellm.exceptions`'
    own base (confirmed by inspecting fully-qualified `__mro__` entries, not
    just `__name__`, which hid this at first). A second guess
    (`openai.OpenAIError`) was *also* wrong, caught only by an actual live
    call with a deliberately invalid key: the SDK wraps every LLM-call
    failure in its own `LLMError` hierarchy
    (`LLMAuthenticationError`/`LLMRateLimitError`/`LLMTimeoutError`/
    `LLMContextWindowExceedError`/`LLMServiceUnavailableError`/
    `LLMBadRequestError`/...) before it ever reaches this code —
    `exc.original_exception` was a real `LLMAuthenticationError`, not a bare
    LiteLLM/OpenAI exception at all. `LLMError` is that hierarchy's one
    common base.

    A no-op fallback (behaves exactly like a bare `conversation.run()` call)
    when `model_chain` is `None` (auto mode off) or `cfg.execution !=
    "local"` (`conversation.switch_llm` doesn't exist on `RemoteConversation`
    — see ROADMAP.md's decisions log).
    """
    while True:
        try:
            conversation.run()
            return
        except ConversationRunError as exc:
            if (
                model_chain is None
                or cfg.execution != "local"
                or not isinstance(exc.original_exception, LLMError)
            ):
                raise
            failed_name = model_chain.current.name
            next_entry = model_chain.advance(
                kind="escalation_api_failure",
                reason=(
                    f"{failed_name} failed with "
                    f"{type(exc.original_exception).__name__}: {exc.original_exception}"
                ),
            )
            if next_entry is None:
                raise
            _emit_notice(
                emit,
                f"Harness: {failed_name} failed "
                f"({type(exc.original_exception).__name__}) — switching to "
                f"{next_entry.name} and retrying.",
            )
            conversation.switch_llm(
                llm_for_entry(cfg, next_entry, usage_id=f"harness:{next_entry.name}")
            )


def _run_with_confirmation(
    conversation: Conversation,
    cfg: Config,
    emit: Callable[[Message], None],
    on_confirm: ConfirmCallback | None,
    deadline: float = float("inf"),
    model_chain: ModelChain | None = None,
) -> str:
    """Drive `conversation.run()` to completion, resolving every
    `AlwaysConfirm` pause along the way instead of leaving it unhandled —
    the actual wiring `HARNESS_CONFIRM_MODE=always` previously lacked (it
    was parsed/validated in `config.py` but never attached to a real
    `ConfirmationPolicyBase`; see ROADMAP.md) — and enforcing the shared,
    task-level `HARNESS_MAX_TASK_SECONDS` wall-clock budget before every
    single `.run()` call it makes, not just the first. A drop-in
    replacement for a bare `conversation.run()` call: when
    `cfg.confirm_mode != "always"` and the budget isn't exhausted, it just
    runs once and returns `"ok"`, identical to the old behavior.

    Returns `"ok"` if the run reached a normal terminal status.  Returns
    `"confirmation_required"` only when the conversation paused for
    confirmation and no `on_confirm` callback was available to answer it.
    Returns `"budget_exhausted"` only when `deadline` (an absolute
    `time.monotonic()` timestamp — see `stream_task`) had already passed
    before a `.run()` call was allowed to start. Either non-`"ok"` value
    means the caller must treat it as its own terminal outcome rather than
    proceeding to task_tracker/project verification against a run that
    never actually continued (or never started another retry). Never
    silently approves a pending action (defeats the safety gate
    `HARNESS_CONFIRM_MODE=always` exists for), never silently lets a run
    keep going past its budget, and never loops forever with nobody able
    to answer a confirmation.

    `deadline` closes a real gap: each resumed `.run()` call gets its own
    fresh `max_iteration_per_run` budget from the SDK — confirmed by
    reading `local_conversation.py`: `iteration` is a local variable reset
    to `0` at the top of every `run()` call, not persisted across calls —
    so `HARNESS_MAX_ITERATIONS` alone cannot bound how long one task runs
    in total across the initial run and every retry phase combined (see
    ROADMAP.md's decisions log for the exact multiplication and why a
    wall-clock budget was chosen over reverse-engineering the SDK's
    internal per-call iteration count). Checking it before *every* `.run()`
    call this function makes — including each confirm/reject round-trip —
    means a long approve/reject back-and-forth can't exceed it either.
    """
    if time.monotonic() >= deadline:
        return _RUN_BUDGET_EXHAUSTED
    _run_conversation_once(conversation, cfg, emit, model_chain)
    if cfg.confirm_mode != "always":
        return _RUN_OK
    while conversation.state.execution_status == _WAITING_FOR_CONFIRMATION:
        pending = _pending_actions(conversation)
        if on_confirm is None:
            summary = "; ".join(_describe_pending_action(a) for a in pending)
            _emit_notice(
                emit,
                "Harness confirm-mode: the agent proposed an action that "
                f"needs approval ({summary}), but no interactive approval "
                "handler is available in this context. Stopping rather "
                "than silently approving it or waiting on confirmations "
                "nobody can answer.",
            )
            conversation.reject_pending_actions(
                "Harness confirm-mode: no approval handler available; rejected automatically."
            )
            return _RUN_CONFIRMATION_REQUIRED
        if time.monotonic() >= deadline:
            _emit_notice(
                emit,
                "Harness: the task's shared wall-clock budget "
                f"(HARNESS_MAX_TASK_SECONDS={cfg.max_task_seconds}) ran out while "
                "waiting on a confirm-mode approval. Stopping.",
            )
            return _RUN_BUDGET_EXHAUSTED
        if on_confirm(pending):
            _run_conversation_once(conversation, cfg, emit, model_chain)
        else:
            conversation.reject_pending_actions("Rejected by the user via harness confirm-mode.")
            _run_conversation_once(conversation, cfg, emit, model_chain)
    return _RUN_OK


def _verify_and_report(
    conversation: Conversation,
    cfg: Config,
    emit: Callable[[Message], None],
    task: str,
    on_confirm: ConfirmCallback | None = None,
    deadline: float = float("inf"),
    model_chain: ModelChain | None = None,
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
    if time.monotonic() >= deadline:
        return TaskOutcome(
            verification_state="budget_exhausted",
            completion_contract=_budget_exhausted_contract(task, cfg),
        )

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
        if model_chain is not None and cfg.execution == "local":
            failed_name = model_chain.current.name
            next_entry = model_chain.advance(
                kind="escalation_quality_failure",
                reason=f"{failed_name}'s fix attempt still failed verification ({run.state}).",
            )
            if next_entry is not None:
                _emit_notice(
                    emit,
                    f"Harness: escalating from {failed_name} to {next_entry.name} "
                    "after a failed verification retry.",
                )
                conversation.switch_llm(
                    llm_for_entry(cfg, next_entry, usage_id=f"harness:{next_entry.name}")
                )
        conversation.send_message(_verification_followup(run))
        run_result = _run_with_confirmation(
            conversation, cfg, emit, on_confirm, deadline, model_chain
        )
        outcome = _outcome_for_run_result(
            run_result, task, cfg, checks=tuple(run.checks), retries_used=retries_used
        )
        if outcome is not None:
            return outcome

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


def _task_tracker_snapshot(conversation: Conversation) -> list | None:
    """Return the task_tracker tool's most recently observed task list (a
    list of TaskItem-shaped objects with `.title`/`.status`/`.notes` —
    `TaskItem` itself isn't part of `openhands.tools.task_tracker`'s public
    `__all__`, so accessed structurally here rather than imported), or
    `None` if the tool was never invoked in this conversation.

    `None` is deliberately not a violation: the tool's own description says
    trivial, single-step tasks don't need it, and `get_default_tools()`
    always includes it whether or not a given task calls for it. Confirmed
    live (not assumed) that `conversation.state.events` holds every
    `ObservationEvent` for the conversation's lifetime — including ones
    from earlier `conversation.run()` calls — and that `ObservationEvent
    .tool_name` for a task_tracker call equals `TaskTrackerTool.name`
    (`"task_tracker"`, derived from the class name, not hardcoded here in
    case a future SDK version renames it).
    """
    snapshot: list | None = None
    for event in conversation.state.events:
        if isinstance(event, ObservationEvent) and event.tool_name == TaskTrackerTool.name:
            snapshot = list(getattr(event.observation, "task_list", []))
    return snapshot


def _pending_task_items(task_list: list) -> list:
    return [item for item in task_list if item.status != "done"]


def _task_tracker_followup(pending: list) -> str:
    lines = "\n".join(
        f"- [{item.status}] {item.title}" + (f" — {item.notes}" if item.notes else "")
        for item in pending
    )
    return (
        "Harness check: your own task_tracker list still has the following "
        f"item(s) not marked done:\n\n{lines}\n\n"
        "If the task is genuinely complete, use task_tracker to mark each "
        "item done (or remove it if it no longer applies) before finishing "
        "again. If an item is truly blocked, say so explicitly in your "
        "final message and explain why — do not leave it as 'todo' or "
        "'in_progress' and declare the task finished anyway."
    )


def _task_tracker_completion_contract(task: str, pending: list) -> CompletionContract:
    pending_desc = "; ".join(f"'{item.title}' ({item.status})" for item in pending)
    return CompletionContract(
        goal=task,
        acceptance_criteria=[
            "The task described in the original request is implemented.",
            ("The agent's own task_tracker list has no items left as 'todo' or 'in_progress'."),
        ],
        verification_checks=[],
        limitations=[
            (
                "The agent's own task_tracker list still shows unfinished "
                f"item(s) after automated follow-up ({pending_desc}); project "
                "verification checks were not run, since completion was never "
                "established."
            )
        ],
    )


def _enforce_task_tracker_completion(
    conversation: Conversation,
    cfg: Config,
    emit: Callable[[Message], None],
    task: str,
    on_confirm: ConfirmCallback | None = None,
    deadline: float = float("inf"),
    model_chain: ModelChain | None = None,
) -> TaskOutcome | None:
    """Post-hoc safety net for `_AUTONOMOUS_SUFFIX`'s "never leave your own
    task_tracker list incomplete" instruction: inspect the agent's own
    task_tracker state directly instead of trusting its finish message —
    the same "verify, don't trust the self-report" principle
    `_verify_and_report` already applies to test results (see ROADMAP.md).

    Returns `None` when there's nothing to enforce (the tool was never
    used, or its last known state already has no pending items) so
    `stream_task` proceeds to `_verify_and_report`; returns a terminal
    `TaskOutcome` only if the agent gets stuck/errors mid-retry, the shared
    task-level budget (`deadline`) runs out, or the retry budget is
    exhausted with items still pending. Reuses `cfg.max_verify_retries` as
    the retry budget — the same "how many automatic fix-then-recheck
    cycles are we willing to spend" question `_verify_and_report` already
    answers for test failures, not a second, near-duplicate config knob
    for this closely related concern.
    """
    if time.monotonic() >= deadline:
        return TaskOutcome(
            verification_state="budget_exhausted",
            completion_contract=_budget_exhausted_contract(task, cfg),
        )

    status = conversation.state.execution_status
    if status in _ABORTED_STATUSES:
        contract = _build_completion_contract(task, None, skipped=False, aborted=True)
        _emit_notice(
            emit,
            "Harness check: the agent's run ended in a "
            f"'{status.value}' state before its task_tracker completion "
            "could be checked. This task needs manual attention.",
        )
        return TaskOutcome(verification_state="stuck", completion_contract=contract)

    snapshot = _task_tracker_snapshot(conversation)
    if snapshot is None:
        return None
    pending = _pending_task_items(snapshot)
    if not pending:
        return None

    attempts_left = cfg.max_verify_retries
    retries_used = 0
    while pending:
        if attempts_left <= 0:
            contract = _task_tracker_completion_contract(task, pending)
            summary = "; ".join(f"'{item.title}' ({item.status})" for item in pending)
            _emit_notice(
                emit,
                "Harness check: the agent's own task_tracker list still "
                f"shows unfinished item(s) after {cfg.max_verify_retries} "
                f"automated follow-up(s) ({summary}). Giving up — this "
                "task needs manual attention.",
            )
            return TaskOutcome(
                verification_state="incomplete",
                completion_contract=contract,
                retries_used=retries_used,
            )
        attempts_left -= 1
        retries_used += 1
        if model_chain is not None and cfg.execution == "local":
            failed_name = model_chain.current.name
            next_entry = model_chain.advance(
                kind="escalation_quality_failure",
                reason=(
                    f"{failed_name} left its own task_tracker list incomplete after a follow-up."
                ),
            )
            if next_entry is not None:
                _emit_notice(
                    emit,
                    f"Harness: escalating from {failed_name} to {next_entry.name} "
                    "after an incomplete task_tracker follow-up.",
                )
                conversation.switch_llm(
                    llm_for_entry(cfg, next_entry, usage_id=f"harness:{next_entry.name}")
                )
        conversation.send_message(_task_tracker_followup(pending))
        run_result = _run_with_confirmation(
            conversation, cfg, emit, on_confirm, deadline, model_chain
        )
        outcome = _outcome_for_run_result(run_result, task, cfg, retries_used=retries_used)
        if outcome is not None:
            return outcome

        retry_status = conversation.state.execution_status
        if retry_status in _ABORTED_STATUSES:
            contract = _build_completion_contract(task, None, skipped=False, aborted=True)
            _emit_notice(
                emit,
                "Harness check: the agent got stuck "
                f"(status '{retry_status.value}') while following up on its "
                f"own incomplete task_tracker list, after {retries_used} "
                "attempt(s). This task needs manual attention.",
            )
            return TaskOutcome(
                verification_state="stuck",
                completion_contract=contract,
                retries_used=retries_used,
            )

        snapshot = _task_tracker_snapshot(conversation) or []
        pending = _pending_task_items(snapshot)

    return None


def _apply_acceptance_checks(
    outcome: TaskOutcome,
    cfg: Config,
    emit: Callable[[Message], None],
    checks: Sequence[AcceptanceCheck] | None,
) -> TaskOutcome:
    """Evaluate caller-supplied `acceptance_checks` against `cfg.workspace`
    and fold the results into `outcome` — applied once, as the very last
    step of `stream_task`, to whichever `TaskOutcome` came back from any
    phase (initial run, task_tracker, or project verification).

    A no-op when `checks` is empty/`None` — this feature is opt-in
    (`acceptance.py`'s own scope note applies: only `file_exists`/
    `file_contains` kinds exist, deliberately no command-execution kind),
    so a caller that never supplies `acceptance_checks` sees zero behavior
    change. Evaluated regardless of `verification_state` — even a stuck or
    incomplete run's workspace is still worth reporting on — but only
    overrides `verification_state` itself when it would otherwise be
    `"verified"` and a *required* check failed: every other state already
    represents some other failure, so there's nothing left to "block".
    """
    if not checks:
        return outcome

    results = evaluate_acceptance_checks(cfg.workspace, checks)
    failed_required = [r for r in results if r.check.required and not r.passed]

    criteria = list(outcome.completion_contract.acceptance_criteria)
    limitations = list(outcome.completion_contract.limitations)
    for result in results:
        label = result.check.description or f"{result.check.kind} {result.check.path}"
        criteria.append(label)
        if not result.passed:
            kind_text = "Required" if result.check.required else "Optional"
            limitations.append(f"{kind_text} acceptance check failed ({label}): {result.detail}")

    new_state = outcome.verification_state
    if failed_required and outcome.verification_state == "verified":
        new_state = "acceptance_failed"
        summary = "; ".join(
            f"{r.check.description or r.check.path}: {r.detail}" for r in failed_required
        )
        _emit_notice(
            emit,
            "Harness acceptance check: the project's own tests/build passed, "
            f"but {len(failed_required)} required acceptance check(s) failed "
            f"({summary}). Not reporting this as verified.",
        )

    contract = replace(
        outcome.completion_contract, acceptance_criteria=criteria, limitations=limitations
    )
    return replace(
        outcome,
        verification_state=new_state,
        completion_contract=contract,
        acceptance_results=tuple(results),
    )


def stream_task(
    task: str,
    cfg: Config | None = None,
    on_message: Callable[[Message], None] | None = None,
    on_confirm: ConfirmCallback | None = None,
    acceptance_checks: Sequence[AcceptanceCheck] | None = None,
    on_awaiting_input: OnAwaitingInput | None = None,
    on_model_choice: OnModelChoice | None = None,
    run_id: str | None = None,
    project: str | None = None,
) -> TaskOutcome:
    """Run a task, invoking `on_message` with each message as it's produced,
    and return the terminal `TaskOutcome` once verification has settled.

    Shared by `run_task` (collects into a list) and the server's WebSocket
    endpoint (pushes each message to the client as it arrives). `on_confirm`
    is only consulted when `cfg.confirm_mode == "always"` (see
    `_run_with_confirmation`); `cli.py` supplies a real terminal-prompting
    implementation, `server.py` leaves it unset. `acceptance_checks` is
    entirely optional — see `_apply_acceptance_checks`. `on_awaiting_input`
    is only consulted when `cfg.interactive` is true, and only around the
    *initial* run — see the interactive loop below and ROADMAP.md's
    decisions log for why task_tracker/verification retries stay fully
    autonomous even in interactive mode. `on_model_choice` is only consulted
    when both `cfg.model_selection == "auto"` and `cfg.interactive` are
    true — see model_selection.py's `ModelChain` for the deterministic
    scoring/fallback-chain design. `run_id` names this run's subfolder under
    `HARNESS_ARTIFACTS_DIR` (see `artifacts.py`) — `server.py` passes its
    own `TaskRecord.id` so a task's artifacts folder matches `GET
    /tasks/{id}`; every other caller gets one minted automatically when
    `cfg.artifacts_dir` is set. Has no effect at all when it's unset.
    `project` is the same project name `cli.py --project`/`server.py`'s
    `project` request field already resolve into `cfg.workspace` before
    calling this. `Config` has no field for the name itself (only the
    already-resolved workspace path), so a caller that knows it passes it
    again here, purely to bucket this run's artifacts folder the same way
    `HARNESS_PROJECTS_DIR` is bucketed.
    """
    if cfg is None:
        cfg = load_config()
    emit = on_message or (lambda _msg: None)
    checkpoint_buffer: list[Message] = []
    all_messages: list[Message] = []

    def on_event(event: Event) -> None:
        if isinstance(event, LLMConvertibleEvent):
            message = event.to_llm_message()
            checkpoint_buffer.append(message)
            all_messages.append(message)
            emit(message)

    # HARNESS_MODEL_SELECTION=auto: pick the best-fit catalog candidate for
    # this task before the Agent/Conversation is even built. The rest of
    # this function keeps using `cfg` unchanged (workspace, execution,
    # confirm_mode, verify_tests, ...) — only the Agent's own LLM is built
    # from a catalog entry's config (see model_selection.config_for_entry).
    model_chain: ModelChain | None = None
    agent_cfg = cfg
    agent_usage_id = "harness"
    if cfg.model_selection == "auto":
        catalog = load_model_catalog(cfg.models_file)
        profile = classify_task(task, catalog)
        ranked = rank_candidates(catalog, profile.weights)
        chosen_index = 0
        reason = f"Task classified as '{profile.name}' (weights: {profile.weights})."
        if cfg.interactive and on_model_choice is not None:
            chosen_index = on_model_choice(ranked, 0, reason)
        model_chain = ModelChain(
            ranked, task_profile=profile.name, weights=profile.weights, start_index=chosen_index
        )
        model_chain.record_initial(reason)
        entry = model_chain.current
        agent_cfg = config_for_entry(cfg, entry)
        agent_usage_id = f"harness:{entry.name}"

    with build_workspace(cfg) as workspace:
        conversation = Conversation(
            agent=build_agent(agent_cfg, usage_id=agent_usage_id),
            callbacks=[on_event],
            workspace=workspace,
            # Previously unset (SDK default 500), so HARNESS_MAX_ITERATIONS
            # was parsed/validated but silently had no effect — found while
            # bounding the verify-and-retry loop above, which reuses the same
            # conversation.run() and needed this to actually mean something.
            max_iteration_per_run=cfg.max_iterations,
        )
        if cfg.confirm_mode == "always":
            # Previously parsed/validated in config.py but never attached to
            # an actual ConfirmationPolicyBase, so it was a silent no-op —
            # see ROADMAP.md. AlwaysConfirm pauses before every tool call;
            # _run_with_confirmation resolves each pause via on_confirm.
            conversation.set_confirmation_policy(AlwaysConfirm())
        # A shared, task-level wall-clock budget spanning every phase below —
        # HARNESS_MAX_ITERATIONS alone only bounds a single conversation.run()
        # call, and runner.py can call that up to three times per task (this
        # initial run, then up to max_verify_retries more in each of the two
        # retry loops below), each getting its own fresh iteration budget
        # from the SDK. See _run_with_confirmation's docstring and
        # ROADMAP.md's decisions log for the exact multiplication this closes.
        deadline = time.monotonic() + cfg.max_task_seconds

        # HARNESS_ARTIFACTS_DIR: mint a run id up front (server.py passes its
        # own TaskRecord.id instead, so a task's folder matches GET
        # /tasks/{id}) and record wall-clock start — a no-op when unset.
        if cfg.artifacts_dir:
            run_id = run_id or str(uuid.uuid4())
            started_at = datetime.now(UTC).isoformat()

        outcome: TaskOutcome | None = None
        captured_error: BaseException | None = None
        try:
            outcome = _run_stream_task_phases(
                conversation,
                cfg,
                emit,
                task,
                on_confirm,
                on_awaiting_input,
                deadline,
                model_chain,
                checkpoint_buffer,
            )
        except BaseException as exc:  # recorded for artifacts.py, then always re-raised as-is
            captured_error = exc
            raise
        finally:
            # Written even when a phase raises (e.g. every candidate in an
            # auto-selection chain failed at the API level) — that's exactly
            # when the escalation history matters most for debugging. See
            # the live repro this fixed in ROADMAP.md's decisions log.
            if model_chain is not None:
                write_model_decisions(cfg.workspace, model_chain.decisions)
            if cfg.artifacts_dir:
                stats = conversation.conversation_stats
                write_run_artifacts(
                    artifacts_dir=cfg.artifacts_dir,
                    run_id=run_id,
                    project=project,
                    task=task,
                    execution=cfg.execution,
                    model=(
                        model_chain.current.model if model_chain is not None else agent_cfg.model
                    ),
                    model_selection=cfg.model_selection,
                    started_at=started_at,
                    ended_at=datetime.now(UTC).isoformat(),
                    messages=[m.model_dump(mode="json") for m in all_messages],
                    outcome=outcome,
                    error=(str(captured_error) if captured_error is not None else None),
                    combined_metrics=stats.get_combined_metrics().get(),
                    per_model_metrics={
                        usage_id: metrics.get()
                        for usage_id, metrics in stats.usage_to_metrics.items()
                    },
                )
        outcome = _apply_acceptance_checks(outcome, cfg, emit, acceptance_checks)
        if model_chain is not None:
            outcome = replace(outcome, model_decisions=tuple(model_chain.decisions))
        return outcome


def _run_stream_task_phases(
    conversation: Conversation,
    cfg: Config,
    emit: Callable[[Message], None],
    task: str,
    on_confirm: ConfirmCallback | None,
    on_awaiting_input: OnAwaitingInput | None,
    deadline: float,
    model_chain: ModelChain | None,
    checkpoint_buffer: list[Message],
) -> TaskOutcome:
    """The initial run, the interactive checkpoint loop, and the two
    autonomous retry phases — split out of `stream_task` purely so the
    `finally: write_model_decisions(...)` wrapping it can run regardless of
    which phase raises, without an extra indent level for all of it.
    """
    conversation.send_message(task)
    run_result = _run_with_confirmation(conversation, cfg, emit, on_confirm, deadline, model_chain)
    outcome = _outcome_for_run_result(run_result, task, cfg)

    # HARNESS_INTERACTIVE=yes, initial run only (see stream_task's
    # docstring and ROADMAP.md's decisions log): the SDK can't tell a
    # genuine clarifying question apart from real completion — both set
    # execution_status = FINISHED identically (see agent.py's
    # _AUTONOMOUS_SUFFIX docstring) — so rather than guessing, always
    # offer the human a checkpoint here and let them decide. Has no
    # effect without a caller-supplied on_awaiting_input (server.py
    # never supplies one; cfg.interactive alone is not enough).
    while (
        outcome is None
        and cfg.interactive
        and on_awaiting_input is not None
        and conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    ):
        narrative = _narrative_text(checkpoint_buffer)
        checkpoint_buffer.clear()
        wait_started = time.monotonic()
        reply = on_awaiting_input(narrative)
        # Time spent waiting on the human doesn't count against the
        # shared task budget — only actual agent run time should.
        deadline += time.monotonic() - wait_started
        if not reply:
            break
        conversation.send_message(reply)
        run_result = _run_with_confirmation(
            conversation, cfg, emit, on_confirm, deadline, model_chain
        )
        outcome = _outcome_for_run_result(run_result, task, cfg)

    if outcome is None:
        outcome = _enforce_task_tracker_completion(
            conversation, cfg, emit, task, on_confirm, deadline, model_chain
        )
    if outcome is None:
        outcome = _verify_and_report(
            conversation, cfg, emit, task, on_confirm, deadline, model_chain
        )
    return outcome


def run_task(
    task: str,
    cfg: Config | None = None,
    on_confirm: ConfirmCallback | None = None,
    acceptance_checks: Sequence[AcceptanceCheck] | None = None,
    on_awaiting_input: OnAwaitingInput | None = None,
    on_model_choice: OnModelChoice | None = None,
    run_id: str | None = None,
    project: str | None = None,
) -> TaskResult:
    messages: list = []
    outcome = stream_task(
        task,
        cfg=cfg,
        on_message=messages.append,
        on_confirm=on_confirm,
        acceptance_checks=acceptance_checks,
        on_awaiting_input=on_awaiting_input,
        on_model_choice=on_model_choice,
        run_id=run_id,
        project=project,
    )
    return TaskResult(messages, outcome)  # last message is the final assistant output
