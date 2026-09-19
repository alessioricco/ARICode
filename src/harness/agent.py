"""Wires the LLM, tools, and skill context into an SDK Agent."""

from __future__ import annotations

from openhands.sdk import Agent, AgentContext

from .config import Config
from .llm import build_llm
from .skills import load_skill_catalog
from .tools import build_tools

# The SDK ends a conversation turn (execution_status -> FINISHED) whenever the
# LLM replies with plain text and no tool call — identically to how it treats
# an actual `finish` tool call (see response_dispatch.py's
# `_handle_content_response`, "LLM produced a message response - awaits user
# input"). This harness's runner calls `conversation.run()` exactly once with
# nobody able to answer a follow-up, so any plain-text reply — whether it's a
# clarifying question or a premature "done" summary — silently ends the run,
# no error raised. Confirmed live twice, two distinct failure modes under the
# same root cause: (1) the agent asked "let me know if you have specific
# preferences" with no file written; (2) after the first fix, the agent built
# 1 of 6 self-tracked task_tracker items (a demo component), then declared
# success and reframed the other 5 — booking logic, backend, responsiveness,
# testing — as "next steps for you," i.e. it self-graded as done instead of
# continuing. Steering the agent to proceed autonomously (and to not
# self-declare completion early) is a system prompt concern, not a runner
# one, hence it lives here rather than in runner.py's control flow.
_AUTONOMOUS_SUFFIX = (
    "You are running non-interactively: no user is available to answer "
    "follow-up questions, approve a plan, or clarify requirements mid-task. "
    "Never stop to ask a clarifying question or wait for confirmation — make "
    "the most reasonable judgment call yourself, note any assumptions in your "
    "final message, and keep working (across multiple tool calls) until the "
    "task described in the first user message is actually done, not merely "
    "planned. If you maintain a task_tracker list, do not end the "
    "conversation while any item is still incomplete — keep working through "
    "the list yourself. Never describe required functionality you have not "
    "yet built as a 'next step', suggestion, or optional enhancement for the "
    "user to do themselves — if it's part of the task, implement it before "
    "finishing. Only end the conversation by calling the `finish` tool, and "
    "only once the work itself is actually complete, not merely planned or "
    "partially demonstrated."
)

# A harness-wide deliverable requirement, not a fix for a stop-condition bug
# (kept separate from _AUTONOMOUS_SUFFIX above for that reason) — every task
# runs against its own project workspace (see cli.py's `--project` /
# server.py's `project`), and nothing else in this harness writes a run/setup
# guide for whatever the agent happens to build there.
_README_SUFFIX = (
    "Before finishing, make sure the project's root directory has a "
    "README.md with clear, concrete instructions for how to install its "
    "dependencies and actually run (or build) what you created — the exact "
    "commands, not general advice — so someone else can follow it without "
    "guessing. Write or update this yourself as part of completing the task, "
    "even if the task description didn't explicitly ask for one."
)

# Same underlying pattern as _AUTONOMOUS_SUFFIX above — the agent acting as if
# a human is present when none is — but at the tool-invocation level rather
# than the conversation level. Confirmed live: asked to scaffold with Vite,
# the agent ran `npm create vite@latest ... -- --template react-ts`, which
# started `create-vite`'s interactive template/linter prompt; with no TTY/
# human to answer it, the prompt auto-cancelled (exit code 0, no files
# created) — even though the command's own output printed the fix verbatim
# ("To create in one go, run: create-vite <DIRECTORY> --no-interactive
# --template <TEMPLATE>"). The agent didn't notice nothing was created and
# just re-ran the identical command, repeating this (200K+ tokens, $0.35+)
# until the user killed the process by hand. `HARNESS_EXECUTION=local` uses a
# subprocess-based terminal, not a real PTY (tmux isn't installed — see
# MANUAL.md "Known limitations"), so an interactive prompt can't be answered
# even in principle; this is a tool-usage habit to fix regardless of PTY
# support, since retrying an unchanged failing command is the real waste.
_NONINTERACTIVE_TOOLING_SUFFIX = (
    "This terminal has no human available to answer an interactive prompt "
    "(a scaffolding wizard, a 'confirm install?' prompt, etc.) — such a "
    "prompt will hang or silently auto-cancel without doing anything, often "
    "while still exiting with status 0. Always invoke CLI tools with their "
    "non-interactive/CI flags instead (e.g. `--yes`, `-y`, `--no-interactive`, "
    "or a `CI=1`/`CI=true` environment prefix); if a command's own output "
    "tells you how to run it non-interactively, use exactly that flag next. "
    "If a command produces no useful output, appears to hang, or you're not "
    "sure it actually did anything, verify the result yourself (e.g. list "
    "the directory you expected it to create) before trusting the exit "
    "code, and do not retry the identical command — change it (add the "
    "missing flag) or take a different approach instead."
)

# A stronger, more direct completion gate than _AUTONOMOUS_SUFFIX's general
# "don't declare early completion" rule above — confirmed live that wasn't
# enough on its own. Asked to build a Tower of Hanoi game, the agent called
# `finish` with a message admitting "there are errors in the tests that need
# further debugging" — an explicit, deliberate `finish` call despite known
# failures, not an accidental plain-text stop. Worse, its own diagnosis was
# wrong: the actual file had an IndentationError and didn't even parse (`def
# main():` left with an empty body), so every test failed at collection, not
# from "sequence handling and state transitions" as it claimed — it never
# re-ran anything to check before writing that summary. This harness also
# runs its own post-hoc test verification as a safety net (see runner.py's
# _verify_tests_and_retry) precisely because this instruction, like the
# others in this file, is a soft nudge the model can still ignore or get
# wrong — but the model should still be told the actual bar plainly.
_VERIFY_BEFORE_FINISH_SUFFIX = (
    "Before calling `finish`, verify your own claims by actually running "
    "them — do not guess, assume, or describe a test failure or bug from "
    "memory. If you wrote or changed tests, run the full test suite (e.g. "
    "via the `run_tests` tool) and read the real output; if you wrote a "
    "program meant to run, actually execute it. A `finish` call is not "
    "appropriate while a test is failing, the program crashes, or the code "
    "does not import/parse cleanly — fix the underlying issue and "
    "re-verify, don't report the failure as something left for later."
)

# The eight lifecycle skills in skills/lifecycle/ (repository-discovery,
# requirements-analysis, implementation-planning, testing-and-verification,
# debugging-and-failure-repair, security-review,
# documentation-and-operational-readiness, completion-and-release-readiness
# — see MANUAL.md "Skills") are legacy-format `.md` files with `triggers:`
# frontmatter (KeywordTrigger), not AgentSkills `SKILL.md` — a deliberate
# switch from this harness's earlier 5-skill set (see ROADMAP.md's decisions
# log): a KeywordTrigger skill auto-injects its full content the moment a
# matching word appears in the task, with no model action required, instead
# of depending on the model choosing to call `invoke_skill` — real,
# code-enforced triggering rather than a hope. That makes most of what the
# old suffix said here (name each skill, tell the model to invoke it by
# name) no longer necessary; what's still worth saying explicitly is the
# surrounding discipline a deterministic trigger can't provide on its own —
# confirmed by reading the SDK source (`skill.py`), not assumed.
_LIFECYCLE_SKILLS_SUFFIX = (
    "Before editing, inspect the repository itself (see the "
    "`repository-discovery` skill if it's already in context) and apply "
    "whatever lifecycle skill guidance becomes relevant as the task "
    "unfolds — most of these activate automatically based on your task, "
    "but act on the lifecycle discipline they describe even for a task "
    "whose wording doesn't happen to match a trigger word, if the "
    "situation still calls for it (e.g. a vague request still needs "
    "requirements pinned down even if it never says 'requirement'). Skip a "
    "skill outright when the task is too small for it to matter — a "
    "one-line fix doesn't need requirements analysis or a release-readiness "
    "review. A skill's guidance is advice for how to do the work, never a "
    "substitute for actually doing it — reading `testing-and-verification`'s "
    "or `security-review`'s guidance does not itself verify or secure "
    "anything; you still have to run the checks and read their real output."
)


def build_agent(cfg: Config) -> Agent:
    # HARNESS_INTERACTIVE=yes (cfg.interactive) drops _AUTONOMOUS_SUFFIX so
    # the agent may pause/ask instead of being told to always push forward
    # — the terminal-loop half that answers a question when it does is
    # runner.py's stream_task interactive checkpoint, not anything here.
    # Every other suffix stays unconditional: none of them are about "is a
    # human present," they're independent policies (README requirement,
    # non-interactive tool flags, verify-before-finish, lifecycle skills).
    suffixes = [_AUTONOMOUS_SUFFIX] if not cfg.interactive else []
    suffixes.extend(
        [
            _README_SUFFIX,
            _NONINTERACTIVE_TOOLING_SUFFIX,
            _VERIFY_BEFORE_FINISH_SUFFIX,
            _LIFECYCLE_SKILLS_SUFFIX,
        ]
    )
    agent_context = AgentContext(
        skills=load_skill_catalog(cfg.skills_dir),
        # Resolved lazily by the Conversation once the real workspace path is
        # known (AgentContext itself doesn't know it yet) — this is what makes
        # a project's AGENTS.md / .agents/skills/ (see skills.write_project_context)
        # apply automatically, on top of the shared catalog above.
        load_project_skills=True,
        system_message_suffix="\n\n".join(suffixes),
    )
    agent = Agent(llm=build_llm(cfg), tools=build_tools(), agent_context=agent_context)
    # HARNESS_CONFIRM_MODE == "always" should attach a confirmation policy that
    # pauses before each tool call. The confirmation-policy API is not yet
    # verified against this SDK version (see CLAUDE.md) — /verify-sdk before
    # wiring it up.
    return agent
