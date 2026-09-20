# Roadmap & Status

Internal planning memory: what's implemented, what's left, known limitations,
and why key decisions were made. This is **not** user docs — see `MANUAL.md`
for how to actually use the harness, and `docs/SPEC.md` for the original
build plan.

> **Maintenance note:** update this file whenever milestone status changes, an
> optional feature is built or explicitly deferred, or a new limitation is
> discovered. Keep entries terse — outcome + one-line why. See `CLAUDE.md`.
>
> **Full history:** every decision below is a compressed summary. The
> complete narrative — alternatives considered, live-verification
> transcripts, exact quotes — for every entry through 2026-09-20 lives in
> `docs/ROADMAP.txt` (a frozen snapshot, not maintained further) and in git
> history. If a compressed entry here is missing detail you need, check
> there before re-deriving it from scratch.

## Milestone status (docs/SPEC.md section 11)

| # | Milestone | Status |
|---|---|---|
| 1 | Scaffold + config | Done |
| 2 | llm/tools/agent/runner + hello-world e2e | Done, verified live |
| 3 | Provider-swap live proof | **Blocked** — needs a 2nd provider key or a local model endpoint; user chose to skip rather than provide one |
| 4 | Custom tool + test | Done — `run_tests_tool` |
| 5 | CLI + README + execution-mode flag | Done, verified live |
| 6 | Optional (section 9) | Docker execution: done. Server mode: done (REST async submit+poll, WS streaming, OpenAI-compatible `/v1/chat/completions`). Skills: done — shared catalog + per-project AGENTS.md. Every section-9 item is done. |

## What's implemented

Per-module reference. See `MANUAL.md` for user-facing behavior; see the
decisions log below for *why* a given design was chosen.

- **`config.py`** — env parsing, model-agnostic (`LLM_MODEL` prefix selects
  provider). `override_llm(cfg, model=, api_key=, base_url=,
  reasoning_effort=)` is the per-request override mechanism shared by
  `cli.py`/`server.py`. `resolve_project_dir(projects_dir, project)` is the
  shared resolver both `--project` and the server's `project` field go
  through: rejects an absolute/`..`-containing name outright, then rejects a
  symlink-resolved (`os.path.realpath`) path that escapes `projects_dir` —
  closes a real path-escape bug (`os.path.join` silently discards the first
  arg when the second is absolute). `max_task_seconds` (`HARNESS_MAX_TASK_SECONDS`,
  default `1800`) is the task-level wall-clock budget `runner.py` enforces
  across every phase. `interactive` (`HARNESS_INTERACTIVE`) opts into the
  initial-run interactive checkpoint and drops `_AUTONOMOUS_SUFFIX`.
- **`acceptance.py`** — optional, caller-supplied machine-checkable
  acceptance criteria, scoped to `file_exists`/`file_contains` only (no
  command-exec kind — see decisions log). Own path-containment check
  (mirrors `resolve_project_dir`'s logic, separate implementation). 1MB read
  cap on `file_contains`. Wired via a new `"acceptance_failed"` terminal
  state, applied once at the very end of `stream_task`, downgrading what
  would otherwise be `"verified"` only.
- **`llm.py` / `tools.py` / `agent.py`** — SDK wiring; default preset tools +
  `run_tests`. `build_llm()` only passes `reasoning_effort` when set (else
  the SDK's own `"high"` default applies — passing an explicit `None` would
  silently override it). `AgentContext.system_message_suffix` carries five
  behavioral policies: `_AUTONOMOUS_SUFFIX` (omitted when `cfg.interactive`),
  `_README_SUFFIX` (leave a real `README.md`), `_NONINTERACTIVE_TOOLING_SUFFIX`
  (use CI/non-interactive flags, don't blindly retry a no-op command),
  `_VERIFY_BEFORE_FINISH_SUFFIX` (run tests before claiming success), and
  `_LIFECYCLE_SKILLS_SUFFIX` (apply lifecycle-skill discipline even
  off-trigger, skip for trivial changes, never substitute for actually
  verifying). `build_agent()`/`build_llm()` take an optional `usage_id` so
  auto model selection's candidates each get a distinct SDK LLM-registry key.
- **`runner.py`** — `stream_task()`/`run_task()` are the core primitives;
  wires `cfg.max_iterations` into `Conversation(max_iteration_per_run=...)`
  (previously unset, silently defaulting to the SDK's `500`). Both return a
  `TaskOutcome` — a bounded six-state verification verdict from
  `_verify_and_report()` (harness-side, no-LLM re-run of the project's own
  checks; doesn't trust the agent's self-report), checking
  `conversation.state.execution_status` for `STUCK`/`ERROR` before verifying
  and after every retry. `_enforce_task_tracker_completion()` runs first: if
  the most recent `task_tracker` observation shows unresolved items, resends
  a follow-up and retries (bounded by `HARNESS_MAX_VERIFY_RETRIES`); an
  unresolved list becomes a terminal `"incomplete"` state, short-circuiting
  verification entirely (an unused or already-`done` tracker is not a
  violation). `HARNESS_CONFIRM_MODE=always` is wired via
  `_run_with_confirmation()`, wrapping every `conversation.run()` call site
  and returning `"ok"`/`"confirmation_required"`/`"budget_exhausted"`; no
  `on_confirm` handler (server mode) rejects the pending action once and
  stops rather than auto-approving or hanging. `HARNESS_MAX_TASK_SECONDS` is
  checked before every `.run()` call across all three call sites. `HARNESS_INTERACTIVE`
  wires an `OnAwaitingInput` checkpoint around the *initial* run only (`cli.py`
  supplies a terminal handler, `server.py` doesn't); the deadline is paused,
  not spent, while waiting on a human reply. `HARNESS_MODEL_SELECTION=auto`
  wires a `ModelChain` (see `model_selection.py`) with automatic fallback on
  a `ConversationRunError` wrapping the SDK's own `LLMError` hierarchy;
  `write_model_decisions()`/artifact-writing both run in a `finally` block
  around the whole task so they still fire when every candidate fails.
  `HARNESS_ARTIFACTS_DIR` mints a `run_id` (or uses the caller's, e.g.
  `server.py`'s `TaskRecord.id`) and writes `metadata.json`/`transcript.json`/
  `metrics.json` via `artifacts.py`.
- **`custom_tools/run_tests_tool.py`** — language-neutral verification in
  four stages: **detection** (`detect_project()` — Python/Node/Go/Rust/
  Java-Maven/Java-Gradle marker walk, plus an `"ambiguous"` state for
  monorepos with sibling manifests at the same shallowest depth), **plan
  discovery** (`discover_verification_plan()`, pure), **execution**
  (`execute_check()`, the only subprocess-spawning stage; `stdin=DEVNULL` by
  default on every check), and **aggregation** (`VerificationRun.state`,
  which a secondary check can only ever pull down from `verified`, never
  promote up to it). `CheckOutcome.status` is a five-value enum
  (`passed`/`failed`/`skipped`/`unavailable`/`timed_out`) so "should be
  checkable but isn't" (missing toolchain) is never confused with "not
  configured" or "ran, nothing to check." `CheckSpec.precomputed` supports a
  pure, no-subprocess static check — used by `entrypoint-ordering`, an
  AST-based check (recursive discovery, root always included) that a Python
  entry point's `if __name__ == "__main__":` guard is its last top-level
  statement. The agent-facing `run_tests` tool keeps its original rich
  Python/Node contract and reuses stages 1–3 for Go/Rust/Java. Java is
  detection-only in this dev environment and the Docker image (neither ships
  a Maven/Gradle toolchain).
- **`cli.py`** — `python -m harness "<task>" [--execution] [--project]
  [--agents-md] [--model] [--api-key] [--base-url] [--reasoning-effort]
  [--interactive] [--require-verification] [--acceptance-checks]
  [--auto-model]`. `task` resolves via `resolve_task_source()` (http(s) URL,
  then existing local file, then literal text — CLI-only). `--require-verification`
  makes an `inconclusive` result exit nonzero too (default behavior
  unchanged otherwise). `--auto-model` sets `cfg.model_selection="auto"`;
  prompts for a choice only when `--interactive` is also set.
- **`model_catalog.py`** — pure data/scoring for `models.yaml`, no SDK
  imports: `load_model_catalog()`, `classify_task()` (keyword triggers, same
  pattern as skills), `score_entry`/`rank_candidates` (weighted sum over a
  profile's declared axes, skips `activated: false` entries entirely).
- **`model_selection.py`** — the SDK-touching layer: `ModelChain` is a
  forward-only cursor over one task's ranked candidates (scored once, never
  re-scored mid-task), `.advance()` logs a `ModelDecisionRecord` (never
  carries secrets). `config_for_entry()` passes `""` (not `None`) for an
  unset field so a keyless local model can't inherit a stale API key.
  `write_model_decisions()` rewrites `MODEL_DECISIONS.md` from the full list
  every time (never appends).
- **`artifacts.py`** — pure file-writing for `HARNESS_ARTIFACTS_DIR`:
  `<artifacts_dir>/<project-or-"_unscoped">/<run_id>/`, own path-containment
  check. No SDK dependency. A sibling top-level directory (mirrors
  `HARNESS_PROJECTS_DIR`'s shape), not nested inside a project.
- **`workspace.py`** — `build_workspace(cfg)` single dispatch point;
  `local` returns a path, `docker` returns a `DockerWorkspace`, both context
  managers.
- **`docker/agent-server.Dockerfile`** — layers our tool source onto
  `ghcr.io/openhands/agent-server`.
- **`server.py`** — `GET /health`, `POST /tasks`, `GET /tasks/{id}`,
  `WS /tasks/stream`, `GET /v1/models` + `POST /v1/chat/completions`
  (OpenAI-compatible, streaming and non-streaming). All task-facing
  endpoints share one background-thread+callback pattern. Accepts the same
  per-request LLM overrides as the CLI (the OpenAI adapter uses distinct
  `llm_*`-prefixed fields since `model` is wire-mandated and echo-only).
  `require_verification`/`acceptance_checks` are supported on the native
  REST/WS API only — not `/v1/chat/completions`, which never surfaces
  `verification_state` at all. `DELETE /tasks/{id}` and
  `DELETE /tasks?project=NAME` back the task-store persistence feature.
- **`task_store.py`** — pluggable persistence (`HARNESS_TASK_STORE`:
  `memory`/`redis`/`sqlite`/`mysql`/`postgres`), one `TaskStore` ABC.
  `sqlite`/`mysql`/`postgres` share one `SQLTaskStore` via SQLAlchemy Core.
  `redis` uses native per-key `EX` expiration instead of a purge sweep.
  `HARNESS_TASK_TTL_SECONDS` (default `0` = forever) governs retention;
  `server.py` runs a 60s sweep only when a TTL is configured.
  `build_task_store(cfg)` is the one factory both `server.py` and
  `admin_cli.py` use.
- **`admin_cli.py`** (`harness-admin`) — maintenance CLI talking directly to
  the store, no running server needed: `show`, `delete-task`,
  `delete-project`, `purge [--ttl-seconds N]` (refuses at `<= 0`).
- **`skills.py`** — two mechanisms: `load_skill_catalog()` loads the shared,
  trigger-based catalog (`skills/`) into every `AgentContext`;
  `write_project_context()` writes a caller-supplied, per-project
  `AGENTS.md` (requires `AgentContext.load_project_skills=True`, which is
  consumed lazily by `LocalConversation`, not validated by `AgentContext`
  itself — easy to silently omit). The catalog includes eight authored,
  legacy-format (`triggers:`/`paths:`) lifecycle skills under
  `skills/lifecycle/` — `repository-discovery`, `requirements-analysis`,
  `implementation-planning`, `testing-and-verification`,
  `debugging-and-failure-repair`, `security-review`,
  `documentation-and-operational-readiness`,
  `completion-and-release-readiness` — which supersede an earlier
  downloaded five-skill `SKILL.md` set (see decisions log).

## Backlog — optional / not yet built

- **ECS/EC2 execution backends** — `workspace.py`'s `build_workspace()` is
  the single dispatch point; adding one is a new branch, not a rewrite.
- **`run_tests`/verification generalization still not fully generic.**
  Covers Python/Node/Go/Rust/Java. Not done: a JS project's actual test
  runner (jest/vitest/etc.) isn't config-parsed, only a defined
  `scripts.test` is run as opaque; no Python lint/typecheck beyond
  ruff/mypy; no JS/TS lint-tool inference from config (see next item); no
  ecosystem beyond these six; Java verification is detection-only here and
  in the Docker image (no Maven/Gradle toolchain in either).
- **Milestone 3 live provider-swap proof** — blocked on a second provider
  key or local model endpoint.
- **JS/TS lint tools aren't auto-detected from config** — a Node project's
  own `scripts.lint`/`scripts.typecheck` *are* run if defined, but there's
  no ESLint/tsconfig-based inference the way `_ruff_configured()` infers
  Python lint relevance with no named script (ESLint config formats/flat-config
  variants make this a harder question than the ruff/mypy case).
- **`/v1/chat/completions` doesn't surface `verification_state`** — its wire
  format has no natural field for it; a caller using only that adapter gets
  the agent's final text with no independent verification signal.

## Known limitations (internal/architectural — see MANUAL.md for user-facing ones)

**SDK/tooling gotchas** (re-verify on any SDK upgrade — see CLAUDE.md golden
rule 2):
- `HARNESS_MAX_ITERATIONS` was parsed but never wired to `Conversation` until
  caught while adding the verification retry loop — **closed**, with a
  regression test asserting `Conversation(max_iteration_per_run=...)` is
  actually called (confirmed it fails without the fix).
- Spec's original custom-tool template didn't match the installed SDK
  (v1.47.0, now pinned exactly): `ToolDefinition` is subclassed with a
  `create(cls, conv_state, **params)` classmethod, not built via a factory
  returning instances.
- `get_default_tools()` lives at `openhands.tools.preset.default`, not
  re-exported from `openhands.tools.preset`.
- `file_editor` requires absolute paths; does not resolve relative to the
  workspace.
- `python-dotenv` does not strip a trailing `# comment` from an otherwise-blank value.
- `sys.executable` inside the PyInstaller-frozen Docker image resolves to
  the frozen binary itself, not a Python interpreter (`_python_command()`
  works around this).
- **A `Message`'s `role` does not indicate where its text lives** — an
  `assistant`-role tool-call message has empty `content`; the actual text
  (including the final "finish" message) comes back as a `tool`-role
  message. Caught only by a live run, not by tests built on the same wrong
  assumption. Any future code reading `Message.role` to find "the answer"
  should exclude only `system`/`user`, not filter to `assistant`.

**The "confident but wrong agent" family** — nine variants found live (plus
one requested proactively), each root-caused and fixed; see `docs/ROADMAP.txt`
for full transcripts if a fix needs revisiting:

1. **Plain-text reply ends a run exactly like `finish`, no error** — the SDK
   sets `FINISHED` whenever the LLM replies with text and no tool call,
   indistinguishable from a real `finish` (confirmed by reading
   `response_dispatch.py`). Confirmed live twice (asked for confirmation
   instead of building; declared 1-of-6 tracker items "done"). Fix:
   `_AUTONOMOUS_SUFFIX`. `HARNESS_INTERACTIVE=yes` is the opt-in escape
   hatch. **Status: fix applied, not re-verified live since.**
2. **Agent runs an interactive CLI tool, the prompt silently auto-cancels
   with no TTY, agent doesn't notice and re-runs the identical command** —
   confirmed live (`npm create vite` interactive prompt, 200K+ tokens
   burned re-running it). Fix: `_NONINTERACTIVE_TOOLING_SUFFIX`. **Status:
   applied, not re-verified live.**
3. **Agent called `finish` admitting known failures, with an invented wrong
   diagnosis** — confirmed live (Hanoi: blamed "sequence handling," the file
   actually had a literal `IndentationError` and never parsed). Fix (two,
   since prompt-only looked insufficient): `_VERIFY_BEFORE_FINISH_SUFFIX`
   plus `runner.py`'s harness-side `_verify_tests_and_retry()` (real,
   no-LLM re-run). **Status: both applied; the harness-side fix confirmed
   against the actual broken repro; not re-verified as a full live run.**
4. **Same pattern, non-Python project, pytest-based verification blind to
   it** — a Vite/React JSX parse error; pytest reported "no tests
   collected" as a false pass. Fix: generalized to run `npm run build` for
   a detected Node project. **Status: applied, unit-tested against real
   npm/node subprocesses; not re-verified against the original repro.**
5. **The verification loop couldn't tell "confirmed working" from "found
   nothing to check," and had no visibility into a `STUCK`/`ERROR` run** —
   fixed with the five-state model (`verified`/`failed`/`inconclusive`/
   `retry_exhausted`/`stuck`) and checking `execution_status`. **Status:
   applied, unit-tested with a fake `Conversation` forcing the real SDK's
   `STUCK`/`ERROR` enum values; not re-verified against a real stuck run.**
6. **Every verification concept was implicitly Python/Node-only** — rebuilt
   around the four named stages and the five-value `CheckOutcome.status`
   enum described above, covering Go/Rust/Java. **Status: applied,
   unit-tested per stage plus real-subprocess runs for every toolchain
   installed in this dev environment; not re-verified against a live
   Go/Rust/Java build task end to end.**
7. **Agent repeatedly re-edited already-correct code chasing a wrong test**
   (Hanoi: the test's own state assumption was wrong, not the
   implementation) **and its diagnostic prose degraded into fluent but
   empty text**, which the SDK's `stuck_detection` didn't catch (it only
   flags exact repetition). Fix: (a) lifecycle skills now say to prove a
   test wrong against its own captured state trace before a second
   re-edit; (b) a new `no_progress` terminal state stops the retry loop the
   moment a fix attempt provably changes nothing in the verification
   output. **Status: (b) applied and verified against the actual on-disk
   repro; (a) is prompt-level, not mechanically testable.**
8. **A generated Hanoi program passed every unit test genuinely, but
   crashed the instant a user picked the interactive `SOLVE` option** —
   `solve()` was defined *after* the `__main__` guard, so `import`ing it
   (what the test does) never hit the `NameError` a direct run does
   immediately; pytest structurally cannot see this bug. Fix: a static
   AST `entrypoint-ordering` check plus a `python <entry point>` smoke-run
   (closed stdin) — the smoke-run alone can't reach `SOLVE` itself;
   `entrypoint-ordering` is what actually catches this bug class. **Status:
   applied, verified against the actual repro (entrypoint-ordering fails
   with the precise diagnosis while pytest passes) plus a synthetic
   regression fixture.**

## Decisions log (why, not just what)

Each entry is the decision plus the load-bearing reason. Full alternatives
considered and live-verification detail: `docs/ROADMAP.txt`.

- **Acceptance criteria: `file_exists`/`file_contains` only, no
  command-exec kind.** An unauthenticated server caller triggering
  harness-run commands would be a strictly worse surface than the agent's
  own gated terminal tool. Own path-containment impl (mirrors
  `resolve_project_dir`, kept separate — different error-message
  contracts). 1MB read cap on `file_contains`. New `"acceptance_failed"`
  state applied once at the end of `stream_task`, not retried, not on
  `/v1/chat/completions` (no field for it there).
- **Global task budget: wall-clock (`HARNESS_MAX_TASK_SECONDS`), not
  iteration counting.** The SDK resets its iteration counter on every
  `conversation.run()` call (confirmed in source) and exposes no way to
  read back consumed iterations; event-counting is provably inexact (a
  plain-text finish produces no `ActionEvent`). Checked in
  `_run_with_confirmation()` before every `.run()` call, and once at entry
  to both retry-loop functions. Default `1800`s, on by default (a safety
  backstop, not a behavior fork).
- **`--require-verification`: opt-in, default unchanged.** Flipping the
  default (`inconclusive` exits `0`) would break existing scripts/CI.
  Threaded through CLI + native REST/WS, not `/v1/chat/completions` (no
  `verification_state` field there to act on).
- **`resolve_project_dir()` closed a real security bug**, not just added
  hardening: `os.path.join(a, b)` silently discards `a` when `b` is
  absolute, so `--project /etc/cron.d` (or the same field in an
  unauthenticated `POST /tasks`) redirected the whole workspace to an
  arbitrary host path; `..` had the same effect. Fixed with string-level
  rejection *and* a resolved-path (`realpath`) containment check — the
  second layer is needed because a plain name with no `..` can still be a
  symlink escaping `projects_dir` (verified live with a real symlink).
  Deliberately doesn't defend against a TOCTOU race (no realistic threat
  here).
- **`HARNESS_CONFIRM_MODE=always`**: mechanics (`AlwaysConfirm`,
  `get_unmatched_actions`, `reject_pending_actions`) verified against SDK
  source before wiring. `_run_with_confirmation()` wraps all three
  `conversation.run()` call sites. No handler (server mode) rejects once
  and returns a new `"confirmation_required"` state rather than
  auto-approving or looping. Known accepted gap: each resumed `run()` call
  gets a fresh iteration budget from the SDK.
- **Task-tracker completion enforcement reads live `conversation.state.events`,
  not the tool's `TASKS.json`** — confirmed the harness's `Conversation(...)`
  call never enables persistence, so that file is never written in normal
  operation. New `"incomplete"` state short-circuits *before* project
  verification (an unresolved tracker means completion was never
  established). Reuses `cfg.max_verify_retries` rather than a new knob. An
  unused or already-`done` tracker is not a violation.
- **Pinned `openhands-sdk`/`openhands-tools` to `==1.47.0` exactly**, not a
  range — this project's verify-first posture depends on not trusting SDK
  symbols we haven't re-checked; a floating range would silently
  reintroduce that risk on a routine install.
- **Docker execution: custom Dockerfile**, not the SDK's `DockerDevWorkspace`
  (scoped to the SDK's own dev environment) or a raw pass-through
  (`register_tool()` only affects the calling process, not the container's
  separate agent-server process).
- **Server mode: both async submit+poll and WS streaming** — a single
  blocking `POST` forces clients to hold an unbounded connection open. Both
  share one background-thread+callback pattern.
- **Skills: two separate mechanisms, not one "smart" one.** Rejected an
  LLM-based skill picker — the SDK's own `KeywordTrigger`/`PathTrigger`
  matching already does this deterministically for free. Kept the shared
  catalog (reusable expertise) and per-project `AGENTS.md` (one project's
  facts) as genuinely different concerns.
- **CLI `task` stays one positional with content-sniffing** (URL/file/literal),
  deliberately not extended to the server — a CLI user benefits from
  avoiding shell tricks; a REST/WS caller is already writing code and can
  fetch content itself.
- **Per-request LLM override on the OpenAI adapter uses separate
  `llm_model`/`llm_api_key`/`llm_base_url` fields**, not the wire-mandated
  `model` field — a strict client may set `model` to an arbitrary
  non-LiteLLM string. `TaskRequest` uses plain `model`/`api_key`/`base_url`
  directly. Both end in one shared `config.override_llm()`.
- **Autonomous-behavior fixes are `system_message_suffix` policies, not
  `runner.py` control flow** — a control-flow "auto-continue" treats the
  symptom (the run stopped) without the cause (model uncertainty), and
  would likely just loop into another clarifying question.
- **Per-project `README.md` requirement is a prompt instruction
  (`_README_SUFFIX`), not a harness-generated template** — the harness has
  no idea what's about to be built; the agent is the only party that knows.
  No harness-side check that a README was actually written (soft
  instruction, like the autonomous fix).
- **Harness-side test verification was built together with its prompt fix**,
  unlike the other autonomy fixes — this symptom (an invented, plausible
  wrong diagnosis) is the model being factually wrong about its own state,
  which a "please verify" prompt cannot fix; only re-running the code can.
  Reuses the SDK's `send_message()`-then-`run()` re-entry (confirmed this
  resets `FINISHED`/`STUCK` back to `IDLE`) so retries keep prior context.
  Bounded by `HARNESS_MAX_VERIFY_RETRIES` (default `2`).
- **Five SDLC skills were downloaded, not authored**, from
  `addyosmani/agent-skills`, per explicit instruction to source real
  content — each renamed (dir + frontmatter) to satisfy the SDK's
  strict-mode validation. **Superseded** by the 8-skill lifecycle set below.
- **`run_tests` v1 generalization: detect pytest vs. Node `npm run build`**,
  not a full test-runner-detection rewrite — `npm run build` catches
  "doesn't compile" bugs a project's actual test runner might not cover,
  with no per-framework parsing needed. Defaults to pytest when neither
  marker is found, to avoid changing behavior for this repo's own tests.
- **Verification loop rework: five-state model + `CompletionContract` +
  `TaskOutcome`/`TaskResult`**, not another incremental patch — needed to
  represent `"inconclusive"` as distinct from `"verified"` (no tests ≠
  proof of correctness) and to aggregate multiple checks. `TaskResult`
  subclasses `list` so every existing caller keeps working unchanged.
  `"failed"` is a transient internal state only, never a final
  `verification_state`. Surfaced through CLI exit codes and server
  TaskRecord/WS, deliberately not `/v1/chat/completions`. Lint/typecheck/
  npm-test are opt-in by project config, never invented; a configured-but-
  not-installed tool is a limitation, not a failure.
- **Language-neutral verification (4 stages + `CheckOutcome` enum)**
  replaces a hardcoded 2-outcome tuple, because "checkable but tool not
  installed" is the *common* case for 3 of 6 languages, not an edge case.
  `timed_out` is a genuinely new terminal state, retried like a failure but
  distinguished from `retry_exhausted` if still timing out after retries.
  Go/Rust/Java got unconditional standard commands only where the
  ecosystem has one true convention; Java has no secondary checks (no one
  standard lint/typecheck tool). Java is honestly detection-only here and
  in the Docker image.
- **Lifecycle skills (8, legacy `triggers:`/`paths:` format) replace the
  earlier 5 downloaded `SKILL.md` skills** — legacy format was required
  because `SKILL.md` has no trigger frontmatter at all (confirmed in
  source: it's always menu-listed, `invoke_skill` is the only access
  path), and a legacy skill with a trigger is *also* still menu-listed, so
  switching cost nothing on discoverability while adding real triggering.
  Content authored fresh, not downloaded, because it states this harness's
  own architecture directly (AGENTS.md mechanism, sibling skill names,
  this project's `TaskOutcome` vocabulary) — no external generic skill
  could state these correctly; the "download, don't invent" rule applies
  to generic reusable expertise, not project-specific facts. Old `SKILL.md`
  dirs for the 4 overlapping names were deleted, not left alongside —
  confirmed live that leaving both caused `load_skill_catalog()`'s
  dict-merge to silently drop the new content. Known limitation:
  `KeywordTrigger` matching is approximate (relevant-but-unworded tasks
  miss the trigger; broad shared words fire incidentally).
- **`no_progress` detection compares structured verification output across
  retries**, not the agent's own message text — no reliable cheap way
  exists to score "does this text still make sense," and output comparison
  also catches a *coherent* agent stuck on an ineffective fix. Fires after
  exactly one no-progress retry (given the default budget of 2, requiring
  two would be nearly vacuous). New terminal state, not folded into
  `retry_exhausted` — "kept changing" and "provably changed nothing" are
  differently-actionable signals.
- **Entry-point checks: both a static AST check and a real smoke-run**
  (user's explicit choice) — static-only misses non-ordering startup
  crashes; smoke-run-only can't see *why* it crashed and (stdin closed)
  can't even reach an interactive branch. Explicitly rejected guessing
  input to drive the interactive session — no general safe way to guess
  what an arbitrary generated program expects. `CheckSpec.precomputed`
  added as a general, reusable extension, not a one-off bypass.
  `stdin=DEVNULL` made the default for every check, closing a latent hang
  risk globally. Entry-point discovery was later made recursive, reusing
  `detect_project()`'s own `_SKIP_DIRS`/depth-limit walk plus one new
  `tests`/`test`-excluding set (a test file's own `__main__` guard isn't
  the program's real entry point).
- **`reasoning_effort` added as a 5th per-request override field**, reusing
  `override_llm()` — the SDK's own field is documented provider-neutral and
  forward-compatible, unlike the other reasoning knobs considered
  (OpenAI-specific, or legacy-Anthropic-only). Deliberately not validated
  against a fixed choice list (the field is intentionally open-ended).
  `build_llm()` only passes it when set, since an explicit `None` would
  silently override the SDK's own `"high"` default.
- **Task-store persistence: pluggable `TaskStore` ABC, 5 backends**, not
  just a TTL purge — scope expanded per user request. SQLAlchemy Core for
  all 3 SQL backends (one shared implementation, upsert via
  try-UPDATE-then-INSERT). Delete-by-project exposed via both REST and CLI
  (different callers need different access). Redis uses native per-key
  TTL, not a purge sweep. `MemoryTaskStore` deep-copies on save/get (a real
  bug self-caught once the coarse lock was replaced by per-backend
  locking). `create_app()` builds the store once at startup — deliberate
  fail-fast over a broken `.env` silently 500ing on first request.
  mysql/postgres verified live against real Docker containers but excluded
  from the automated suite (too heavy a CI requirement); sqlite/redis are
  tested for real.
- **Monorepo ambiguity: new `"ambiguous"` `ProjectDetection` state**,
  fixing a real bug — `detect_project()`'s claimed "shallowest wins" wasn't
  actually true across different DFS branches (`os.walk` fully explores a
  first-visited branch before a shallower sibling). Fixed by grouping
  matches by depth during one walk. The workspace root (depth 0) is always
  returned immediately and never "ambiguous" — an explicit `--project`
  already told the harness what the project is. Resolves to the same
  synthetic `"unavailable"` shape already used for `"unknown"` — no
  `runner.py`/`CompletionContract` changes needed.
- **Interactive mode (`HARNESS_INTERACTIVE`): checkpoint wraps only the
  initial run**, not the task_tracker/verify retry loops — the smallest
  change covering the two failure modes already on record, without the
  real design question of how a human reply should interleave with the
  harness's own automated retry follow-ups. `HARNESS_MAX_TASK_SECONDS` is
  paused (not spent) while blocked on a human reply. No new suffix added —
  only `_AUTONOMOUS_SUFFIX` is dropped when interactive. Has no effect
  without a caller-supplied callback (`server.py` never supplies one).
- **Deterministic auto model selection (`HARNESS_MODEL_SELECTION=auto`)**:
  deterministic weighted scoring, not an LLM router — no extra model call,
  consistent with the earlier skills decision. One static ranked list
  computed once per task, walked forward under failure, never re-scored
  (the resulting caveat — fallback picks next-best-fit, not necessarily
  more capable — is documented, not solved). Real API keys live directly
  in `models.yaml` (gitignored like `.env`). Visibility:
  `MODEL_DECISIONS.md` in the project workspace plus
  `TaskOutcome.model_decisions`. Docker gets the initial pick but not
  mid-task fallback — `RemoteConversation`'s Python client doesn't expose
  `switch_llm` even though the remote agent-server's REST API does;
  documented rather than built against private SDK internals. The
  retriable exception type is the SDK's own `LLMError` hierarchy — took two
  rounds of live verification to find (not `litellm.exceptions.APIError`,
  not `openai.OpenAIError`, both invisible once the SDK wraps LLM-call
  failures itself). `write_model_decisions()` runs in the whole task's
  `finally` block after a live-caught bug: an exhausted fallback chain
  previously raised before the file was ever written.
- **Per-run artifacts directory (`HARNESS_ARTIFACTS_DIR`): a separate
  sibling top-level directory**, not nested inside each project — mirrors
  `HARNESS_PROJECTS_DIR`'s shape. `conversation.conversation_stats`/
  `usage_to_metrics` (confirmed in SDK source) gives a full per-model
  cost/token breakdown for free, since model-selection candidates already
  have distinct `usage_id`s. `MODEL_DECISIONS.md` stays put (not moved
  here). A run ID is minted by `stream_task` when none is given;
  `server.py` passes its own `TaskRecord.id` instead. A live-caught bug:
  artifact-writing was moved into the same `finally` block as
  `write_model_decisions()` after an exhausted model chain was found to
  raise before anything was written.
- **`models.yaml` gained a per-model `activated` (bool, default `true`)
  field**, filtered in `rank_candidates()` (not at load time, so a
  deactivated entry still gets schema validation and the duplicate-name
  check). Raises a specific error if every model ends up deactivated.
- **Excluded the 3 third-party `SKILL.md` directories** (`frontend-design`/
  `webapp-testing`/`web-artifacts-builder`) via `.gitignore` + `git rm
  --cached` (untracked, not deleted) — each actually carries Apache-2.0,
  not MIT as MANUAL.md had claimed (fixed in the same change). Scoped only
  to the 3 directories with their own `LICENSE.txt` — the lifecycle/git/
  python-web/testing skills are authored for this project.
- **Project renamed `coding-agent-harness` → ARICode — branding only**
  (repo name, `pyproject.toml` distribution name, Docker image tag
  default, `server.py` title/`owned_by`, doc titles), the shallowest of
  several scoped options. Deliberately unchanged: `import harness`,
  `python -m harness`, every `HARNESS_*` env var, `harness-admin` — a
  distribution name can differ from its import name, zero backward-compat
  impact.
