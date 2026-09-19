# TODO — Candidate Features

Feature backlog for the harness, classified by priority. Derived from
`ROADMAP.md`'s existing backlog/known-limitations, plus a review of the
current codebase (`src/harness/`, `skills/`, `tests/`). This file is a
planning aid, not a replacement for `ROADMAP.md` — once a feature here is
scoped or built, move its record there per `CLAUDE.md`'s maintenance rule.

Classification: **must** (blocks a stated spec acceptance criterion or
carries real correctness/safety risk) | **nice to have** (clear value, no
urgency) | **later** (real but low-impact, or blocked on something outside
our control).

---

## must

### 1. ~~Pin exact `openhands-sdk` / `openhands-tools` versions~~ — DONE
Pinned both to `==1.47.0` (the version already verified throughout
`ROADMAP.md`'s SDK-drift entries) in `pyproject.toml`. Re-resolved
(`uv pip install -e ".[dev]"`) and full suite re-run (240 passed). See
`ROADMAP.md` decisions log for the full rationale.

### 2. ~~`HARNESS_CONFIRM_MODE=always` — wire the pause-before-tool-call gate~~ — DONE
Verified the SDK's confirmation-policy API live (`AlwaysConfirm`,
`get_unmatched_actions`, `reject_pending_actions`) before wiring it. Added
`_run_with_confirmation()` in `runner.py`, wrapping all three
`conversation.run()` call sites; `cli.py` supplies a real terminal
approve/reject prompt. A pending action with no available handler (e.g.
server mode) rejects once and reports a new `"confirmation_required"`
state rather than silently approving or hanging. Confirmed live via the
real CLI, both approve and reject paths, in an isolated workspace with
zero effect on this repo. See `ROADMAP.md` decisions log and `MANUAL.md`
"Confirmation mode".

### 3. ~~Harness-side `task_tracker`-completion enforcement~~ — DONE
Added `_enforce_task_tracker_completion()` (`runner.py`), which runs right
after the agent's first turn and before project verification: scans
`conversation.state.events` for the most recently observed `task_tracker`
tool state, and if any item is still `todo`/`in_progress`, sends a
follow-up and re-runs the agent (bounded by `HARNESS_MAX_VERIFY_RETRIES`).
An unresolved list becomes a new `"incomplete"` terminal `verification_state`
that skips project verification entirely (a task the agent's own tracking
says isn't finished can't be meaningfully "verified" by tests). Verified
live end-to-end: reproduced the exact failure (create 2 tasks, leave one
`todo`, call finish anyway), confirmed the harness caught it and the agent
completed the item on the follow-up. See `ROADMAP.md` decisions log and
`MANUAL.md` "Test verification" → "Task-tracker completion".

### 4. ~~Test coverage for `HARNESS_MAX_ITERATIONS`~~ — DONE
Added `tests/test_runner.py::test_stream_task_wires_max_iterations_to_conversation`
— a no-LLM unit test (monkeypatches `Conversation`/`build_agent`/
`build_workspace`) asserting `stream_task()` passes
`max_iteration_per_run=cfg.max_iterations` to `Conversation(...)`. Confirmed
it actually catches the original regression class by temporarily reverting
the fix and observing the test fail. See `ROADMAP.md` Known Limitations.

### 5. Server mode has no authentication and accepts caller-supplied LLM overrides
`server.py` defaults to binding `127.0.0.1` (safe out of the box), but
`--host` exists specifically to allow non-local binding, and once that
happens every endpoint is wide open: `POST /tasks` lets an unauthenticated
caller run an arbitrary task through an agent that has a **terminal tool**
— i.e. arbitrary shell command execution on the host — and `TaskRequest`/
`ChatCompletionRequest` also accept caller-supplied `api_key`/`llm_api_key`
and `base_url`/`llm_base_url` overrides with no validation, so a remote
caller can redirect outbound LLM calls to an arbitrary URL. This is
already named in `MANUAL.md`'s Known Limitations ("No authentication on
any server-mode endpoint") but isn't tracked as an actionable fix anywhere.
Minimal remediation: a shared-secret header check (e.g.
`HARNESS_SERVER_API_KEY`, checked via a FastAPI dependency — plain FastAPI,
no SDK verification needed) gating all task-submission routes, plus a loud
`MANUAL.md` warning against `--host 0.0.0.0` without one.

### 6. ~~Project path containment and symlink protection~~ — DONE
Added `resolve_project_dir(projects_dir, project)` in `config.py`, the one
shared resolver both `cli.py`'s `--project` and `server.py`'s `project`
request field now go through: rejects an absolute or `..`-containing
`project` name outright, then rejects a resolved, symlink-followed
(`os.path.realpath`) path that falls outside `projects_dir`, raising
`ConfigError` either way. Verified live (the real bug's exact repro:
`--project /etc/cron.d`, `../escaped`, and a real on-disk symlink escape
via `tmp_path` — all rejected; a symlink that stays inside `projects_dir`
still works). 9 new tests across `test_config.py`/`test_cli.py`/
`test_server.py`. See `ROADMAP.md` decisions log and `MANUAL.md`
"Projects: one subfolder per generated project".

### 7. ~~Reconsider treating `inconclusive` as a nonzero (unsuccessful) exit by default~~ — DONE
Asked the user which of three shapes they wanted (flip the default,
opt-in flag, leave as-is) since this genuinely changes CLI/API behavior
for existing callers — chose **opt-in flag, default unchanged**. Added
`--require-verification` (CLI) and `require_verification` (`POST /tasks`/
`WS /tasks/stream`, default `false`): an `inconclusive` result becomes
exit `1` / `status: "failed"` / a `"type": "error"` WS frame when set,
unchanged otherwise. Every other `verification_state` is unaffected.
Verified live via the real CLI (same task, same inconclusive result,
`--require-verification` flipping exit 0 → 1). 13 new tests across
`test_cli.py`/`test_server.py`. See `ROADMAP.md` decisions log and
`MANUAL.md` "CLI reference"/"Server mode".

### 8. ~~Add a global task execution budget~~ — DONE
Added `HARNESS_MAX_TASK_SECONDS` (default `1800`) — a shared, wall-clock
task-level budget, chosen over reverse-engineering a cumulative iteration
count (the SDK exposes no API to read back iterations consumed by a past
`.run()` call, and an event-counting proxy would systematically undercount
the exact "plain-text reply with no tool call" case already documented as
a live gotcha in this file). `_run_with_confirmation()` — already the
single choke point wrapping every `conversation.run()` call — now checks
an absolute `time.monotonic()` deadline before each call, including every
confirm-mode approve/reject round-trip, and returns a three-way result
(`"ok"`/`"confirmation_required"`/`"budget_exhausted"`) instead of a bool;
`_verify_and_report`/`_enforce_task_tracker_completion` also check it once
at entry. Exhausting it produces a new `"budget_exhausted"` terminal
state, wired into `cli.py`'s nonzero-exit set. Verified live end-to-end
(`HARNESS_MAX_TASK_SECONDS=1`: real work completes, then reports
`budget_exhausted` with exit `1`; default budget behaves as before) — this
same live check caught a real omission (the CLI exit-code tuple wasn't
updated on the first pass), fixed and re-verified, with a dedicated
regression test confirmed to fail against the unfixed code. See
`ROADMAP.md` decisions log and `MANUAL.md` "Task budget".

### 9. ~~Add machine-checkable acceptance criteria~~ — DONE (scoped down)
Added `src/harness/acceptance.py`: opt-in, caller-supplied acceptance
checks, evaluated by the harness after the run and folded into a new
`TaskOutcome.acceptance_results` field. Deliberately scoped to
`file_exists`/`file_contains` only — no "run a command" kind, exactly the
risk this item itself called out: a harness-triggered command-execution
surface compounding with server mode's missing auth (item #5). `path` is
always contained to the task's workspace (mirrors `resolve_project_dir`'s
symlink-aware containment check, item #6). A failing *required* check
downgrades an otherwise-`"verified"` result to a new `"acceptance_failed"`
terminal state; every other state is left alone. Wired into both `cli.py`
(`--acceptance-checks`, file-or-inline JSON) and `server.py`
(`TaskRequest.acceptance_checks`, `POST /tasks`/`WS /tasks/stream`), not
the OpenAI-compatible adapter (same reasoning as `require_verification`).
Verified live end-to-end via both the real CLI and a real running server
(`curl` against `POST /tasks`/`GET /tasks/{id}`). See `ROADMAP.md`
decisions log and `MANUAL.md` "Acceptance checks".

---

## nice to have

### 10. Milestone 3 — live provider-swap proof
Currently blocked, not missing by design: needs a second LLM provider key
or a local model endpoint to actually exercise. Proves the model-agnostic
invariant (the project's core selling point) end-to-end rather than by
code inspection alone. Low effort once a key/endpoint is available — mark
as ready-to-do rather than actively schedule.

### 11. JS/TS lint config inference (ESLint, tsconfig)
Node projects only get lint/typecheck verification today if `package.json`
explicitly names a `scripts.lint`/`scripts.typecheck` entry — there's no
equivalent of Python's `_ruff_configured()` that infers "lint is relevant
here" from an `.eslintrc*`/flat-config file with no named script. Improves
verification coverage for a common real-world project shape, but not a
correctness risk on its own (worst case: a check is skipped, not falsely
passed) — flat-config variants make "is lint configured" a genuinely
harder question than the Python case, so it's real scoped work, not quick.

### 12. Wider JS test-runner detection (jest/vitest/mocha)
`npm run build`/`scripts.test` are run as opaque commands today; the
actual test runner and its pass/fail parsing (the rich detail pytest
verification already gets) isn't detected. Would bring Node verification
to parity with the Python path's granularity. Scoped as a real "unscoped"
item in `ROADMAP.md` since Milestone 4 — worth doing, not urgent, since the
build-script check already catches the concrete failure class that
motivated Node verification in the first place (parse errors).

### 13. Surface `verification_state` on the OpenAI-compatible `/v1/chat/completions` adapter
The native REST/WS API (`GET /tasks/{id}`, `WS /tasks/stream`) already
exposes the seven-state verification verdict; the OpenAI-shaped adapter
doesn't, since its wire format has no natural field for it. A caller using
only that adapter (e.g. an IDE plugin expecting OpenAI's shape) currently
gets the agent's own unverified self-report with no independent signal —
same trust gap `_verify_and_report()`/`_enforce_task_tracker_completion()`
were built to close everywhere else, just not reachable from this one
entry point. Needs a deliberate wire-format extension decision (e.g. a
custom field or a trailing system message), not a quick patch.

### 14. ~~Server-mode task-registry TTL purge~~ — DONE
Expanded well beyond the original "just a TTL purge" scope, per an explicit
follow-up ask: a pluggable `task_store.py` with five backends (`memory`
default, `redis`, `sqlite`, `mysql`, `postgres`), fully configurable via
`.env` (`HARNESS_TASK_STORE*`), a `HARNESS_TASK_TTL_SECONDS` retention
setting (`0` = keep forever), and delete-by-project exposed both ways —
`DELETE /tasks?project=NAME` / `DELETE /tasks/{id}` on `server.py`, and a
new `harness-admin` CLI (`show`/`delete-task`/`delete-project`/`purge`).
sqlite/mysql/postgres share one SQLAlchemy Core implementation; redis uses
native per-key TTL instead of a purge sweep. Verified live against real
sqlite and redis-backed servers (task create/poll/delete/delete-by-project
over real HTTP, plus Redis's native TTL actually expiring a key), and
mysql/postgres against real Docker containers during development. See
`ROADMAP.md`'s decisions log for the two `AskUserQuestion` calls (SQLAlchemy
Core vs. three hand-written backends; REST vs. CLI vs. both for
delete-by-project) and the `MemoryTaskStore` thread-safety fix found along
the way.

### 15. ECS/EC2 (or other remote) execution backend
`workspace.py`'s `build_workspace()` is already a single dispatch point —
adding a backend is one new branch plus a new `HARNESS_EXECUTION` value,
not a rewrite. Useful if tasks need to run somewhere other than
local/Docker (e.g. ephemeral cloud runners for parallel tasks), but
nothing today demonstrates a concrete need for it.

### 16. Dedicated live test for the entrypoint-ordering / smoke-run checks
`entrypoint-ordering` and the Python smoke-run are unit-tested against a
synthetic fixture and verified against one real historical repro
(`projects/hanoi/`), but not yet re-verified against a *fresh* live agent
run end-to-end. Cheap to do, closes the "not yet re-verified live" caveat
that recurs across nearly every fix logged in `ROADMAP.md`'s Known
Limitations section.

### 17. Per-task cost ceiling (`HARNESS_MAX_COST_USD`)
`HARNESS_MAX_ITERATIONS` bounds how many *iterations* a run can take, but
not how many *dollars* it can spend — a single expensive iteration (a huge
context, a costly reasoning-effort setting) isn't capped by an iteration
count at all. Confirmed the SDK actually tracks this per conversation:
`conversation.conversation_stats.get_combined_metrics().accumulated_cost`
is a real, live-verified field (`ConversationStats`/`Metrics` in
`openhands.sdk.llm.utils.metrics`). Checking it between retry cycles — the
same points `_verify_and_report`/`_enforce_task_tracker_completion` already
re-check `execution_status` — would let a runaway-cost run stop cleanly
with a new terminal state instead of relying solely on the iteration cap
as a cost proxy.

### 18. Add CI (GitHub Actions) running tests and lint on push/PR
The repo is hosted on GitHub (`origin` points to
`github.com/alessioricco/coding-agent-harness`) but has no
`.github/workflows` at all — the 249-test suite and `ruff check`/
`format --check` only run when a human or agent remembers to run them
locally, per `CLAUDE.md`'s own working-style rule. A minimal workflow
(`uv pip install -e ".[dev]"` + `uv run pytest -q` + `uv run ruff check .`
on push/PR) would catch a regression before it lands rather than relying
on manual discipline every session.

### 19. Optional interactive mode (`HARNESS_INTERACTIVE=yes` / `--interactive`), default off
Today the harness is always fully autonomous: `agent.py`'s
`_AUTONOMOUS_SUFFIX` explicitly tells every agent "there's no user to ask,
proceed on your own judgment, only stop via `finish`" — necessary because
`runner.py` calls `conversation.run()` once with nobody able to answer a
follow-up, and (per `ROADMAP.md`'s Known Limitations) the SDK itself can't
tell a genuine clarifying question apart from real completion — both a
plain-text reply and a `finish` call set `execution_status = FINISHED`
identically. That's the right default for unattended/CI use, but it means
a human running the CLI interactively has no way to answer a question the
agent would otherwise have asked, or to redirect it mid-task, without
killing the run and starting a whole new task from scratch.

Proposed shape, opt-in only (current behavior stays the default exactly as
today): a new flag (`HARNESS_INTERACTIVE` / CLI `--interactive`) that, when
set, (a) drops `_AUTONOMOUS_SUFFIX` from the system prompt so the agent is
allowed to pause and ask instead of being told to always push forward, and
(b) has `cli.py` echo the agent's last message and prompt at the terminal
whenever a run reaches `FINISHED`, giving the human a chance to type a
reply (`conversation.send_message()` + `conversation.run()` again) or
press Enter to end the task normally. Needs a UI-agnostic hook
into `stream_task` (e.g. an optional `on_awaiting_input` callback) rather
than reading `stdin` inside `runner.py` directly, since `runner.py` is also
used by `server.py`, which has no terminal to prompt at — server mode would
simply leave this unset and keep today's always-autonomous behavior
regardless of the flag. Worth doing because it turns an existing, already-
documented SDK-level ambiguity (can't tell "asking a question" from
"actually done") from a workaround-only problem into a genuine, opt-in
feature — but real design work (the callback shape, how much of the
verification/task-tracker retry loops still apply once a human is in the
loop) belongs in a proper plan before implementation, not a quick patch.

### 20. Unify timeout-safe verification for the agent-facing tool
The harness-side verification pipeline's own `execute_check()` converts
subprocess timeouts and launch errors into structured `CheckOutcome`
values and closes stdin — but the legacy agent-facing `run_tests` tool's
`_run_pytest`/`_run_npm_build` paths still call a bare
`subprocess.run(..., timeout=300)` with no `except
subprocess.TimeoutExpired` and no closed stdin, so they can raise instead
of returning a useful observation, and can hang on stdin the same way
`execute_check` was fixed to avoid. Route pytest, npm, and the other
supported checks through the common executor, with closed stdin, CI/
non-interactive environment handling, bounded output, and process-group
cleanup. Add focused tests for timeout and missing-executable behavior.

### 21. Handle ambiguous nested projects and monorepos explicitly
`detect_project()` walks the tree and returns on the *first* matching
manifest it finds (shallowest wins, then declaration order, then walk
order) — it has no concept of "multiple candidate projects" at all. In a
monorepo or workspace containing multiple applications, this can silently
verify the wrong child project and produce a misleading result. Prefer an
explicit project root when one is supplied; otherwise aggregate compatible
projects or return an `ambiguous`/`inconclusive` result listing the
candidates instead of choosing one based on directory-walk order. Add
fixtures with multiple manifests.

---

## later

### 22. Interactive-session smoke testing (drive `SOLVE`-style prompts)
Explicitly rejected as unscoped in `ROADMAP.md`'s decisions log: there's no
general, safe way to guess what an arbitrary generated program expects to
read on stdin, so a wrong guess produces either a misleading failure or
false confidence. Would need either the agent's own declared interaction
script or genuine interactive-session automation — real, open-ended
design work, not a quick addition. Documented as a known permanent gap
rather than a near-term goal.

### 23. Non-Python/Node/Go/Rust/Java ecosystems (Ruby, PHP, .NET, C/C++, …)
Verification is intentionally scoped to "where project metadata makes the
commands unambiguous" (per the original ask). Extending further is
legitimate but speculative — no current task or project in this repo's
history has needed it, so building it now is pure speculative coverage.

### 24. Java verification in this project's own dev/CI environment
Detection is solid; actual `mvn`/`gradle` execution is untested here
because neither toolchain is installed in this dev machine or the Docker
agent-server image. Low urgency: fixing it means adding a Maven/Gradle
toolchain to the Docker image and dev setup for a language this project
has never actually been asked to build, not fixing a code defect.

### 25. Recursive entry-point discovery
Entry points are only checked at a Python project's root directory, not
recursively — deliberate, since a deeper walk risks matching an unrelated
`__main__` guard inside a vendored dependency. Revisit only if a real
nested-entry-point project is actually seen; no evidence of that yet.

### 26. `reasoning_summary` / `extended_thinking_budget` / `enable_encrypted_reasoning` wiring
The SDK exposes three more reasoning-related `LLM` fields beyond
`reasoning_effort`. Deliberately left unwired: `reasoning_effort` is the
only one that's provider-agnostic (matches the model-agnostic invariant)
and the one the SDK's own docs recommend for new integrations; the others
are provider-specific or legacy. Only worth adding if a specific provider
integration actually needs one.

### 27. Protect verification infrastructure from silent agent changes
The agent can modify or remove tests, build scripts, lint configuration, or
type-check configuration before the harness verifies the project. Prompt
guidance says not to weaken checks (e.g. `skills/lifecycle/testing-and-
verification.md`), but the harness has zero mechanical enforcement of
this — a direct gap against `CLAUDE.md`'s golden rule 6 ("harness code
must enforce iteration limits, verification, retries, and explicit
completion states wherever possible"). Later, record relevant verification
files before the run, compare them after edits, and report or reject
changes to those files unless the task explicitly requested them. This
requires a careful policy for legitimate test and configuration changes,
so it is not a small patch.
