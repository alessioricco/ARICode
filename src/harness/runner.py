"""Runs a task end to end and returns/streams the conversation's messages.

Result is captured via an event callback — there is no `conversation.result`.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from openhands.sdk import Conversation, Event, LLMConvertibleEvent, Message, TextContent

from .agent import build_agent
from .config import Config, load_config
from .custom_tools.run_tests_tool import RunTestsAction, RunTestsExecutor, RunTestsObservation
from .workspace import build_workspace

# pytest's own exit code for "ran successfully but collected zero tests" — not
# a failure, just nothing to verify (a non-Python project, or one with no
# tests). Every other nonzero exit code means tests actually ran and
# something is wrong (failures, errors, or a collection-time ImportError/
# SyntaxError — exactly the kind of thing this feature exists to catch, see
# _verify_tests_and_retry's docstring).
_PYTEST_NO_TESTS_COLLECTED = 5


def _tests_are_failing(result: RunTestsObservation) -> bool:
    if result.check_kind == "npm_build":
        return result.exit_code != 0
    if result.check_kind == "none":
        return False
    return result.exit_code not in (0, _PYTEST_NO_TESTS_COLLECTED)


def _run_project_tests(working_dir: str) -> RunTestsObservation | None:
    """Run the project's own verification directly — no LLM/tool-call round
    trip. Delegates to `RunTestsExecutor`, which picks pytest or `npm run
    build` depending on what the project looks like (see run_tests_tool.py).

    None (treated as inconclusive, not a failure) if the check can't even be
    invoked (e.g. it hangs past the executor's own timeout), so a
    verification-step glitch doesn't abort an otherwise-successful task.
    """
    try:
        return RunTestsExecutor(working_dir)(RunTestsAction())
    except Exception:  # noqa: BLE001 - a verification glitch must not abort the task
        return None


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

# The JS/TS analogue of _SYNTAX_ERROR_MARKERS above, for `npm run build`
# failures — confirmed live against the actual error class this
# generalization was built for: a Vite/oxc parse error ("Adjacent JSX
# elements must be wrapped in an enclosing tag") from unclosed/sibling JSX
# in App.tsx.
_JS_SYNTAX_ERROR_MARKERS = ("PARSE_ERROR", "SyntaxError", "Unexpected token")


def _verification_followup(result: RunTestsObservation) -> str:
    if result.check_kind == "npm_build":
        guidance = (
            "Fix the underlying issue yourself — do not delete the "
            "offending code or disable/skip the build step to make it pass "
            "— and only stop once `npm run build` actually succeeds. Make a "
            "minimal, targeted edit at the exact file/line the error points "
            "to rather than rewriting the whole file from scratch."
        )
        if any(marker in result.output for marker in _JS_SYNTAX_ERROR_MARKERS):
            guidance += (
                " This is a JavaScript/TypeScript syntax or parse error: "
                "open the exact file and line the error names and fix only "
                "that structural issue (e.g. wrap adjacent JSX elements in "
                "a single enclosing tag or fragment `<>...</>`)."
            )
        return (
            "Automated verification ran the project's build after you "
            f"finished, and it is still failing ({result.summary}).\n\n"
            f"{result.output}\n\n"
            f"{guidance}"
        )
    guidance = (
        "Fix the underlying issue yourself — do not just weaken, skip, or "
        "delete the failing test to make it pass — and only stop once the "
        "tests actually pass. Make a minimal, targeted edit at the exact "
        "location the error points to rather than rewriting the whole file "
        "from scratch — a full rewrite risks losing or duplicating code you "
        "already had correct and introducing a new bug elsewhere."
    )
    if any(marker in result.output for marker in _SYNTAX_ERROR_MARKERS):
        guidance += (
            " This specific failure is a Python syntax/indentation error, "
            "not a logic bug: open the file, view the exact line(s) the "
            "traceback names, and fix only that structural issue "
            "(indentation level, a misplaced statement, a missing block) — "
            "verify the file parses (e.g. `python -m py_compile "
            "<file>`) before moving on."
        )
    return (
        "Automated verification ran the project's test suite after you "
        "finished, and it is still failing "
        f"({result.summary or f'exit code {result.exit_code}'}).\n\n"
        f"{result.output}\n\n"
        f"{guidance}"
    )


def _verify_tests_and_retry(
    conversation: Conversation, cfg: Config, emit: Callable[[Message], None]
) -> None:
    """Post-hoc safety net for agent.py's `_VERIFY_BEFORE_FINISH_SUFFIX`: run
    the project's tests ourselves instead of trusting the agent's self-report.

    Confirmed live this matters, not just in theory: a real run finished with
    a `hanoi.py` that had an `IndentationError` (the file didn't even parse)
    while the agent's own finish message described "errors...around sequence
    handling and state transitions" — a misdiagnosis of a problem it never
    actually re-ran to check. Bounded by `cfg.max_verify_retries` so a
    project whose tests are simply wrong, or a bug the model can't fix,
    doesn't loop forever.
    """
    if cfg.verify_tests != "always":
        return

    working_dir = os.path.abspath(cfg.workspace)
    attempts_left = cfg.max_verify_retries
    while True:
        result = _run_project_tests(working_dir)
        if result is None or not _tests_are_failing(result):
            return
        if attempts_left <= 0:
            what, verb = ("build", "is") if result.check_kind == "npm_build" else ("tests", "are")
            emit(
                Message(
                    role="assistant",
                    content=[
                        TextContent(
                            text=(
                                f"Harness verification: the project's {what} {verb} "
                                f"still failing after {cfg.max_verify_retries} "
                                "automated fix attempt(s) "
                                f"({result.summary or f'exit code {result.exit_code}'}). "
                                "Giving up — this project needs manual attention."
                            )
                        )
                    ],
                )
            )
            return
        attempts_left -= 1
        conversation.send_message(_verification_followup(result))
        conversation.run()


def stream_task(task: str, cfg: Config | None = None, on_message: Callable[[Message], None] | None = None) -> None:
    """Run a task, invoking `on_message` with each message as it's produced.

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
        _verify_tests_and_retry(conversation, cfg, emit)


def run_task(task: str, cfg: Config | None = None) -> list:
    messages: list = []
    stream_task(task, cfg=cfg, on_message=messages.append)
    return messages  # last message is the final assistant output
