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

### 19. ~~Optional interactive mode (`HARNESS_INTERACTIVE=yes` / `--interactive`), default off~~ — DONE
Implemented per a plan reviewed with the user first (`EnterPlanMode` +
`AskUserQuestion`, matching this item's own "real design work... before
implementation" ask). Confirmed scope: the interactive checkpoint wraps
only the *initial* `conversation.run()` — once the human ends the
back-and-forth (or there was nothing to ask), the existing
`_enforce_task_tracker_completion`/`_verify_and_report` retry loops proceed
completely unchanged, still fully autonomous. `HARNESS_INTERACTIVE`
(`Config.interactive`, default `False`) drops `agent.py`'s
`_AUTONOMOUS_SUFFIX`; `runner.py`'s `stream_task`/`run_task` gained an
`on_awaiting_input: Callable[[str], str | None]` parameter consulted only
around that initial run; `cli.py`'s new `--interactive` flag wires a real
terminal handler (`_prompt_for_continuation`, mirroring
`_confirm_pending_actions`'s shape) — `server.py` supplies none, matching
`HARNESS_CONFIRM_MODE=always`'s existing server-mode gotcha. Time spent
waiting on the human's reply is excluded from `HARNESS_MAX_TASK_SECONDS`
(the deadline is pushed forward by the wait duration). Verified live twice
against a real LLM call: a single-round accept-and-finish, and a
multi-round conversation where a typed follow-up made the agent create a
second file in the same running conversation. See `ROADMAP.md`'s decisions
log for the full design reasoning.

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

### 21. ~~Handle ambiguous nested projects and monorepos explicitly~~ — DONE
`detect_project()` now collects every manifest match grouped by depth
during its single tree walk instead of returning on the first one a DFS
happens to visit (which wasn't actually "shallowest wins" as claimed —
`os.walk` fully explores one branch before a shallower sibling). The
workspace root itself, if it has a manifest, is authoritative and returned
immediately (the caller's `HARNESS_WORKSPACE`/`--project` already *is* the
explicit root — nothing further to disambiguate). Otherwise, if more than
one distinct directory matches at the shallowest depth found, detection
reports a new `ProjectDetection(language="ambiguous", candidates=(...))`
instead of picking one; `discover_verification_plan()` turns that into a
single `unavailable` check naming every candidate's language and path,
reusing the existing `"unknown"`-project/`inconclusive` plumbing rather
than inventing a new terminal state. Same-directory multi-marker ties
(e.g. both `pyproject.toml` and `package.json` in one directory) are
unaffected — still resolved by marker priority order, a deliberately
separate, narrower case. 7 new tests (sibling projects, listing-order
independence, explicit-root-wins, shallower-single-match-wins, plan
discovery, full-pipeline integration, agent-facing tool coverage);
verified live against a real on-disk `frontend/`+`backend/` monorepo
fixture. See `ROADMAP.md`'s decisions log for the full reasoning.

### 22. Populate real token usage on the OpenAI-compatible adapter
`/v1/chat/completions`'s response always hardcodes
`{"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}` — flagged
in `MANUAL.md`'s Known Limitations as "usage isn't tracked," phrased as if
the SDK doesn't expose it. It does: item #17's own research confirmed
`conversation.conversation_stats.get_combined_metrics().accumulated_cost`
is a real, live-verified field on `ConversationStats`/`Metrics`
(`openhands.sdk.llm.utils.metrics`), and that same object almost certainly
carries token counts alongside cost (worth a quick `/verify-sdk` check of
its exact shape). Populating a real `usage` object from it is a
self-contained, low-risk fix — no wire-format change, just no longer
lying about a field OpenAI clients already expect to be meaningful.

### 23. `TaskStore`/`harness-admin` has no way to list existing tasks or projects
Every operation on the task store — `harness-admin show/delete-task`,
`DELETE /tasks/{id}`, `DELETE /tasks?project=`, `GET /tasks/{id}` — requires
already knowing the exact task ID or project name ahead of time. There's no
`list()`/`list_projects()` on the `TaskStore` ABC or any of its four
backends, so an operator with a shared redis/sqlite/mysql/postgres store
and no external record of what's in it has no way to discover what to
clean up. Each backend would need a real (if backend-specific) listing
query — sqlite/mysql/postgres via a plain `SELECT DISTINCT`, redis via a
`SCAN` over `harness:task:*`/`harness:project:*` keys (memory is trivial).
Worth doing since the whole feature's stated purpose (item #14) is
letting a long-running deployment manage its own history.

### 24. Surface `model_decisions` on `GET /tasks/{id}` (server mode)
`TaskOutcome.model_decisions` and `MODEL_DECISIONS.md` already exist, but
`server.py`'s `TaskRecord`/task-store schema were deliberately
left untouched when auto model selection was built — adding the field
would mean a real migration story for already-deployed sqlite/mysql/
postgres task stores (an existing on-disk table lacks the new column;
`metadata.create_all()` only creates missing *tables*, not columns on an
existing one), which wasn't in scope for that change and needs its own
deliberate design rather than being bundled into an unrelated feature.

### 25. `harness-admin`: preview model classification/ranking without running a task
Tuning a `models.yaml`'s `ratings`/`task_profiles` today means actually
running a real (billed) task and reading `MODEL_DECISIONS.md` after the
fact to see what got picked and why. `model_catalog.py`'s
`classify_task()`/`rank_candidates()` are already pure, no-network
functions — a `harness-admin classify "<task text>" [--models-file PATH]`
subcommand printing the matched profile, its weights, and the full ranked
table would let someone iterate on the catalog for free.

### 26. ~~Remove dead code: `_find_marker_dir` in `run_tests_tool.py`~~ — DONE
Removed — it was defined but never called anywhere in `src/`, confirmed
again immediately before deleting it. No behavior change (full suite,
536 tests, still passes); `ruff check`/`format --check` unaffected (the
file's few pre-existing lint findings are unrelated to this function).

---

## later

### 27. Interactive-session smoke testing (drive `SOLVE`-style prompts)
Explicitly rejected as unscoped in `ROADMAP.md`'s decisions log: there's no
general, safe way to guess what an arbitrary generated program expects to
read on stdin, so a wrong guess produces either a misleading failure or
false confidence. Would need either the agent's own declared interaction
script or genuine interactive-session automation — real, open-ended
design work, not a quick addition. Documented as a known permanent gap
rather than a near-term goal.

### 28. Non-Python/Node/Go/Rust/Java ecosystems (Ruby, PHP, .NET, C/C++, …)
Verification is intentionally scoped to "where project metadata makes the
commands unambiguous" (per the original ask). Extending further is
legitimate but speculative — no current task or project in this repo's
history has needed it, so building it now is pure speculative coverage.

### 29. Java verification in this project's own dev/CI environment
Detection is solid; actual `mvn`/`gradle` execution is untested here
because neither toolchain is installed in this dev machine or the Docker
agent-server image. Low urgency: fixing it means adding a Maven/Gradle
toolchain to the Docker image and dev setup for a language this project
has never actually been asked to build, not fixing a code defect.

### 30. ~~Recursive entry-point discovery~~ — DONE
`_find_python_entrypoints()` now walks the whole project tree, bounded by
the same `_MAX_SCAN_DEPTH` `detect_project()` uses, filtered by a new
`_ENTRYPOINT_SKIP_DIRS = _SKIP_DIRS | {"tests", "test"}` (the vendor/build
skip list this item flagged as the risk, plus a project's own tests
directory — a test script's own `unittest.main()` guard is not the
program's entry point). Returns paths relative to the project root, so a
root-level entry point is unchanged (a bare filename) and a nested one
reports as e.g. `src/app/main.py`; downstream consumers needed no changes.
9 new tests added (nested discovery, vendor/node_modules/venv/hidden-dir
skipping, tests-dir skipping, depth bound, ordering-check and smoke-run
command construction for a nested path); full suite 545 passed. Verified
live against a synthetic nested project (vendor/tests correctly excluded)
and against the existing `projects/hanoi/` root-level repro (unchanged
`["hanoi.py"]`). See `ROADMAP.md` decisions log and `MANUAL.md` "Custom
tools".

### 31. `reasoning_summary` / `extended_thinking_budget` / `enable_encrypted_reasoning` wiring
The SDK exposes three more reasoning-related `LLM` fields beyond
`reasoning_effort`. Deliberately left unwired: `reasoning_effort` is the
only one that's provider-agnostic (matches the model-agnostic invariant)
and the one the SDK's own docs recommend for new integrations; the others
are provider-specific or legacy. Only worth adding if a specific provider
integration actually needs one.

### 32. Protect verification infrastructure from silent agent changes
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

### 33. Auto model selection: proactive per-task_tracker-item switching
Explicitly deferred as a real v1.1 idea during the auto-model-selection
design conversation, not forgotten: today's fallback only ever triggers
on a failure
(API-level or a failed verification/task_tracker retry) — it never
proactively picks a different model for a different *kind* of sub-step
within the same task with nothing having gone wrong yet (e.g. a cheap
model for scaffolding, a stronger one for the one genuinely hard
algorithm). The natural hook is the SDK's own `task_tracker` tool, since
each item already has its own title/description to classify against — but
the agent can update the tracker many times within a single
`conversation.run()` call, and the harness doesn't regain control until
that whole call ends, so this needs the harness to interrupt/resume a run
around tracker-item transitions (`conversation.pause()`/`interrupt()`
exist on the SDK and are worth checking), not just swap between
already-separate `.run()` calls the way today's escalation does. Real,
open-ended design work.

### 34. Auto model selection: mid-task fallback under `HARNESS_EXECUTION=docker`
Confirmed live against the SDK source: `RemoteConversation`'s Python
client has no `switch_llm`/`switch_profile` method, even though the remote
agent-server it talks to already exposes a matching `POST
/conversations/{id}/switch_llm` endpoint server-side. Not building this
against `RemoteConversation`'s private `_client`/`_id` attributes was a
deliberate call (unstable, undocumented internals — exactly the SDK-drift
risk golden rule 2 exists to avoid). Revisit once a future `openhands-sdk`
release adds the public method; at that point this is additive (one
`cfg.execution` check to remove), not a redesign.

### 35. Auto model selection: escalate toward capability, not just next-best-fit
The fallback chain is one static list ranked by fit for the *classified
task type*, walked forward under any failure — so falling back on a
verification failure moves to the next-best-*fit* candidate, which isn't
guaranteed to be more capable in an absolute sense (only usually is, in a
catalog where a task profile's weighted axes happen to correlate with
overall model strength). A more deliberate version would re-rank toward
raw capability specifically for a *quality*-triggered escalation (not an
API-level one, where "try any working alternative" is the actual goal) —
this needs its own design pass (what does "capability" mean independent of
task fit — a separate rating axis? the same axes unweighted?), not a
quick patch to the existing chain-walking logic.

### 36. Auto model selection: a condenser safety net for context-window mismatches
Falling back to a model with a meaningfully smaller context window than
whatever's accumulated in the conversation so far isn't explicitly
handled — today it just surfaces as a context-overflow error, which is
itself an API-level failure and so falls through to the *next* candidate
anyway (a real, if inelegant, self-healing property already confirmed
live). Configuring an `LLMSummarizingCondenser` when auto-selection is on,
and/or ordering the fallback chain to avoid switching down to a much
smaller window without first calling the SDK's `conversation.condense()`,
would handle this more deliberately. Not urgent: the current behavior
degrades gracefully rather than silently breaking.
