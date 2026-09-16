# TODO — Candidate Features

Feature backlog for the harness, classified by priority. Derived from
`ROADMAP.md`'s existing backlog/known-limitations, plus a review of the
current codebase (`src/harness/`, `skills/`, `tests/`). This file is a
planning aid, not a replacement for `ROADMAP.md` — once a feature here is
scoped or built, move its record there per `CLAUDE.md`'s maintenance rule.

Classification: **must** (blocks a stated spec acceptance criterion or
carries real correctness/safety risk) | **nice to have** (clear value, no
urgency) | **not urgent** (real but low-impact, or blocked on something
outside our control).

---

## must

### 1. ~~Pin exact `openhands-sdk` / `openhands-tools` versions~~ — DONE
Pinned both to `==1.47.0` (the version already verified throughout
`ROADMAP.md`'s SDK-drift entries) in `pyproject.toml`. Re-resolved
(`uv pip install -e ".[dev]"`) and full suite re-run (240 passed). See
`ROADMAP.md` decisions log for the full rationale.

### 2. `HARNESS_CONFIRM_MODE=always` — wire the pause-before-tool-call gate
Parsed and validated in `config.py` but never connected to an actual
confirmation policy (`ROADMAP.md` backlog). This is a named acceptance
surface in `docs/SPEC.md` section 8 ("Confirmation: `HARNESS_CONFIRM_MODE=always`
attaches a policy that pauses before each tool call") that currently does
nothing — a user who sets it gets silent no-op safety, not the safety they
asked for. Needs `/verify-sdk` on the SDK's confirmation-policy API first.

### 3. Harness-side `task_tracker`-completion enforcement
Currently only a prompt-level mitigation (`_AUTONOMOUS_SUFFIX`) for the
"agent declares done with incomplete `task_tracker` items" failure mode
logged in `ROADMAP.md`. Prompt steering is soft and has already been shown
insufficient for a structurally similar issue (the `finish`-while-tests-fail
case, which needed real harness-side verification, not just a suffix). A
mechanical check — inspect the tracker state after `conversation.run()` and
require completion before trusting `finish` — closes a real
correctness/trust gap, not just a nice-to-have polish item.

### 4. Test coverage for `HARNESS_MAX_ITERATIONS`
This was silently a no-op for the entire project history until a very
recent fix (`runner.py` now passes `max_iteration_per_run=cfg.max_iterations`
to `Conversation(...)`), and `docs/SPEC.md` section 12 lists "reliably
bounds runaway loops" as an explicit acceptance criterion. No test asserts
on iteration-capping behavior today, so a future regression (e.g. an SDK
upgrade renaming/removing the kwarg) would go undetected exactly the same
way the original bug did. A live/integration test closing this is cheap
insurance against repeating a bug already paid for once.

---

## nice to have

### 5. Milestone 3 — live provider-swap proof
Currently blocked, not missing by design: needs a second LLM provider key
or a local model endpoint to actually exercise. Proves the model-agnostic
invariant (the project's core selling point) end-to-end rather than by
code inspection alone. Low effort once a key/endpoint is available — mark
as ready-to-do rather than actively schedule.

### 6. JS/TS lint config inference (ESLint, tsconfig)
Node projects only get lint/typecheck verification today if `package.json`
explicitly names a `scripts.lint`/`scripts.typecheck` entry — there's no
equivalent of Python's `_ruff_configured()` that infers "lint is relevant
here" from an `.eslintrc*`/flat-config file with no named script. Improves
verification coverage for a common real-world project shape, but not a
correctness risk on its own (worst case: a check is skipped, not falsely
passed) — flat-config variants make "is lint configured" a genuinely
harder question than the Python case, so it's real scoped work, not quick.

### 7. Wider JS test-runner detection (jest/vitest/mocha)
`npm run build`/`scripts.test` are run as opaque commands today; the
actual test runner and its pass/fail parsing (the rich detail pytest
verification already gets) isn't detected. Would bring Node verification
to parity with the Python path's granularity. Scoped as a real "unscoped"
item in `ROADMAP.md` since Milestone 4 — worth doing, not urgent, since the
build-script check already catches the concrete failure class that
motivated Node verification in the first place (parse errors).

### 8. Surface `verification_state` on the OpenAI-compatible `/v1/chat/completions` adapter
The native REST/WS API (`GET /tasks/{id}`, `WS /tasks/stream`) already
exposes the five/six-state verification verdict; the OpenAI-shaped adapter
doesn't, since its wire format has no natural field for it. A caller using
only that adapter (e.g. an IDE plugin expecting OpenAI's shape) currently
gets the agent's own unverified self-report with no independent signal —
same trust gap `_verify_and_report()` was built to close everywhere else,
just not reachable from this one entry point. Needs a deliberate wire-format
extension decision (e.g. a custom field or a trailing system message), not
a quick patch.

### 9. Server-mode task-registry TTL purge
`server.py`'s task registry is in-memory, per-process, and unbounded —
fine for a dev/demo server, a real liability for anything long-running
(memory grows forever; task IDs live forever). A TTL-based purge is a
small, self-contained improvement; a persistent store (Redis/DB) is a
bigger step and only worth it if multi-worker/restart-survives use is
actually needed.

### 10. ECS/EC2 (or other remote) execution backend
`workspace.py`'s `build_workspace()` is already a single dispatch point —
adding a backend is one new branch plus a new `HARNESS_EXECUTION` value,
not a rewrite. Useful if tasks need to run somewhere other than
local/Docker (e.g. ephemeral cloud runners for parallel tasks), but
nothing today demonstrates a concrete need for it.

### 11. Dedicated live test for the entrypoint-ordering / smoke-run checks
`entrypoint-ordering` and the Python smoke-run are unit-tested against a
synthetic fixture and verified against one real historical repro
(`projects/hanoi/`), but not yet re-verified against a *fresh* live agent
run end-to-end. Cheap to do, closes the "not yet re-verified live" caveat
that recurs across nearly every fix logged in `ROADMAP.md`'s Known
Limitations section.

---

## not urgent

### 12. Interactive-session smoke testing (drive `SOLVE`-style prompts)
Explicitly rejected as unscoped in `ROADMAP.md`'s decisions log: there's no
general, safe way to guess what an arbitrary generated program expects to
read on stdin, so a wrong guess produces either a misleading failure or
false confidence. Would need either the agent's own declared interaction
script or genuine interactive-session automation — real, open-ended
design work, not a quick addition. Documented as a known permanent gap
rather than a near-term goal.

### 13. Non-Python/Node/Go/Rust/Java ecosystems (Ruby, PHP, .NET, C/C++, …)
Verification is intentionally scoped to "where project metadata makes the
commands unambiguous" (per the original ask). Extending further is
legitimate but speculative — no current task or project in this repo's
history has needed it, so building it now is pure speculative coverage.

### 14. Java verification in this project's own dev/CI environment
Detection is solid; actual `mvn`/`gradle` execution is untested here
because neither toolchain is installed in this dev machine or the Docker
agent-server image. Low urgency: fixing it means adding a Maven/Gradle
toolchain to the Docker image and dev setup for a language this project
has never actually been asked to build, not fixing a code defect.

### 15. Recursive entry-point discovery
Entry points are only checked at a Python project's root directory, not
recursively — deliberate, since a deeper walk risks matching an unrelated
`__main__` guard inside a vendored dependency. Revisit only if a real
nested-entry-point project is actually seen; no evidence of that yet.

### 16. `reasoning_summary` / `extended_thinking_budget` / `enable_encrypted_reasoning` wiring
The SDK exposes three more reasoning-related `LLM` fields beyond
`reasoning_effort`. Deliberately left unwired: `reasoning_effort` is the
only one that's provider-agnostic (matches the model-agnostic invariant)
and the one the SDK's own docs recommend for new integrations; the others
are provider-specific or legacy. Only worth adding if a specific provider
integration actually needs one.
