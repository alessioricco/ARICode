# Roadmap & Status

Internal planning memory: what's implemented, what's left, known limitations,
and why key decisions were made. This is **not** user docs — see `MANUAL.md`
for how to actually use the harness, and `docs/SPEC.md` for the original
build plan. This file is the living, evolving companion to that static plan.

> **Maintenance note:** update this file whenever milestone status changes, an
> optional feature is built or explicitly deferred, or a new limitation is
> discovered. Keep entries terse — outcome + one-line why. See `CLAUDE.md`.

## Milestone status (docs/SPEC.md section 11)

| # | Milestone | Status |
|---|---|---|
| 1 | Scaffold + config | Done |
| 2 | llm/tools/agent/runner + hello-world e2e | Done, verified live |
| 3 | Provider-swap live proof | **Blocked** — needs a 2nd provider key or a local model endpoint; user chose to skip rather than provide one |
| 4 | Custom tool + test | Done — `run_tests_tool` |
| 5 | CLI + README + execution-mode flag | Done, verified live |
| 6 | Optional (section 9) | Docker execution: done. Server mode: done (REST async submit+poll, WS streaming, OpenAI-compatible `/v1/chat/completions`). Skills (formerly "microagents"): done — shared catalog + per-project AGENTS.md. Every section-9 item is now done. |

## What's implemented

- `config.py` — env parsing, model-agnostic (`LLM_MODEL` prefix selects provider).
  `override_llm(cfg, model=, api_key=, base_url=, reasoning_effort=)` returns
  a copy of `Config` with only the given fields replaced — the per-request/
  per-run override mechanism shared by `cli.py` and `server.py` (see
  MANUAL.md "Switching LLM provider / model" → "Per-request override").
  `reasoning_effort` (from `LLM_REASONING_EFFORT`) is provider-neutral and
  deliberately not validated against a fixed choice list — see decisions log.
  `resolve_project_dir(projects_dir, project)` is the one shared resolver
  `cli.py`'s `--project` and `server.py`'s `project` request field both go
  through (closing a real path-escape bug, not just adding hardening — see
  "Known limitations" below and decisions log): rejects an absolute or
  `..`-containing `project` name outright, then rejects a resolved,
  symlink-followed (`os.path.realpath`) path that falls outside
  `projects_dir`, raising `ConfigError` either way so both callers keep
  using the error type they already catch. `max_task_seconds` (from
  `HARNESS_MAX_TASK_SECONDS`, default `1800`) is the shared, task-level
  wall-clock budget `runner.py` enforces across every phase of one task —
  see "Decisions log" below for why this, not a reverse-engineered
  cumulative iteration count, closes the "one `HARNESS_MAX_ITERATIONS`
  doesn't bound a whole task" gap. `interactive` (from `HARNESS_INTERACTIVE`,
  default `False`) opts into `runner.py`'s initial-run interactive
  checkpoint and drops `agent.py`'s `_AUTONOMOUS_SUFFIX` — see the matching
  entries below and decisions log for the confirmed scope (initial run
  only) and why it has no effect without a caller-supplied
  `on_awaiting_input`.
- `acceptance.py` — optional, caller-supplied machine-checkable acceptance
  criteria for one specific task, evaluated by the harness after the run,
  never agent-facing or agent-defined. Deliberately scoped to two check
  kinds only — `file_exists`, `file_contains` — with no "run this command"
  kind, even though that was one of the kinds originally proposed; see
  "Decisions log" below for the security reasoning (a caller-triggered
  command-execution surface compounding with server mode's lack of auth).
  `path` is always resolved relative to the task's workspace via its own
  containment check — mirrors `config.resolve_project_dir`'s logic
  (reject absolute/`..` outright, then a symlink-followed containment
  check) but is a separate implementation, not a shared one, since the two
  have different roots and error-message context (same "duplication over
  premature abstraction" call as elsewhere in this codebase).
  `AcceptanceCheckError` subclasses `ValueError` so both `cli.py`'s and
  `server.py`'s existing exception handling catches it with zero new
  wiring. Wired into `runner.py` via `TaskOutcome.acceptance_results` and
  a new `"acceptance_failed"` terminal state (only reachable by
  downgrading what would otherwise be `"verified"` — every other state is
  already a failure of some kind and is left untouched), applied once as
  the very last step of `stream_task` regardless of which phase produced
  the underlying outcome. `cli.py`'s `--acceptance-checks` and
  `server.py`'s `TaskRequest.acceptance_checks` both accept the same JSON
  shape; the OpenAI-compatible adapter does not (same reasoning as
  `require_verification`: it doesn't surface `verification_state`, so a
  flag whose only effect is on that value would have no observable
  effect there).
- `llm.py` / `tools.py` / `agent.py` — SDK wiring; default preset tools + `run_tests`.
  `llm.py`'s `build_llm()` only passes `reasoning_effort` to the SDK's `LLM(...)`
  when `cfg.reasoning_effort` is set, so the SDK's own default (`"high"`)
  applies when it's unset — see decisions log for the omit-vs-`None` subtlety.
  `agent.py`'s `AgentContext.system_message_suffix` also carries five
  behavioral policies, not just SDK plumbing: `_AUTONOMOUS_SUFFIX` (see
  "Known limitations" below), `_README_SUFFIX`, which tells every agent to
  leave a `README.md` with concrete run instructions in the project root
  before finishing even when the task didn't ask for one (see MANUAL.md
  "Projects" → "README.md"), `_NONINTERACTIVE_TOOLING_SUFFIX`, which tells
  the agent to use CLI tools' non-interactive/CI flags and not blindly retry
  a command that produced no visible result, `_VERIFY_BEFORE_FINISH_SUFFIX`,
  which tells the agent to actually run its tests/program before calling
  `finish` rather than describing untested claims as fact (see "Known
  limitations" below for both), and `_LIFECYCLE_SKILLS_SUFFIX` (formerly
  `_SDLC_SKILLS_SUFFIX`), which tells the agent to inspect the repository
  before editing, apply a lifecycle skill's guidance even off-trigger when
  the situation still calls for it, skip a skill for trivial changes, and
  treat skill guidance as never a substitute for actually running the
  checks it describes — see "Decisions log" below for why this no longer
  needs to name skills individually or reference `invoke_skill`.
  `build_agent()` now includes `_AUTONOMOUS_SUFFIX` conditionally — omitted
  when `cfg.interactive` is true, unconditional otherwise — every other
  suffix stays unconditional regardless of interactive mode; see the
  matching `runner.py` entry and decisions log. `build_agent()`/
  `llm.py`'s `build_llm()` both also gained an optional `usage_id: str =
  "harness"` parameter (defaulted, every existing call site unaffected) —
  auto model selection gives each catalog candidate a distinct
  `usage_id` so the SDK's LLM registry (keyed by `usage_id`, per
  `switch_llm`'s own docstring) never confuses one candidate's config for
  another's mid-task.
- `runner.py` — `stream_task()` (callback-per-message) is the shared primitive;
  `run_task()` wraps it for the CLI's collect-and-return use case. Also now
  wires `cfg.max_iterations` into `Conversation(max_iteration_per_run=...)`
  (previously unset, silently defaulting to the SDK's 500 — see "Known
  limitations" below). Both now always return a `TaskOutcome` (`run_task`'s
  is attached as `.outcome` on the `TaskResult` list subclass it returns, so
  every pre-existing `list[Message]` caller keeps working unchanged) — the
  bounded, six-state verification verdict from `_verify_and_report()`, a
  harness-side, no-LLM re-run of the project's own checks via
  `run_tests_tool.run_full_verification()`, resending real failures/timeout
  notices and re-running the agent (bounded by `HARNESS_MAX_VERIFY_RETRIES`,
  retried for both `failed` and `timed_out` `VerificationRun` states)
  instead of trusting the agent's self-report, and checking
  `conversation.state.execution_status` before verifying (and again after
  every retry) so a stuck/errored run is reported as such rather than
  silently "completed" — see MANUAL.md "Test verification" and "Decisions
  log" below for the full architecture. Also runs
  `_enforce_task_tracker_completion()` right after the initial
  `conversation.run()`, before `_verify_and_report()` ever runs: scans
  `conversation.state.events` for the most recently observed
  `task_tracker` tool `ObservationEvent` and, if any item is still
  `"todo"`/`"in_progress"`, resends a follow-up and re-runs the agent
  (bounded by the same `HARNESS_MAX_VERIFY_RETRIES` budget, checking
  `execution_status` before and after each retry the same way
  `_verify_and_report` does) instead of trusting a `finish` call over the
  agent's own task list. A `None` return (tool never used, or already
  complete) is not a violation and falls through to `_verify_and_report`
  as before; an unresolved list becomes the new `"incomplete"` terminal
  `verification_state`, short-circuiting project verification entirely
  (an incomplete task list means completion was never established, so
  running tests to "verify" it would be building on a false premise). See
  "Decisions log" below for why this reads live conversation events rather
  than the tool's persisted `TASKS.json`. Also wires
  `HARNESS_CONFIRM_MODE=always` to an actual `AlwaysConfirm` confirmation
  policy (`conversation.set_confirmation_policy(...)`) — previously
  parsed/validated in `config.py` but never attached to anything, a silent
  no-op (see "Known limitations" below). `_run_with_confirmation()` wraps
  every `conversation.run()` call in `runner.py` (the initial run, and
  both retry loops') and drives the SDK's approve/reject mechanics
  (`conversation.run()` again to approve, `reject_pending_actions()` +
  `run()` to reject — both confirmed live, not assumed, by reading
  `local_conversation.py`'s own `run()` loop): given an `on_confirm`
  callback, it consults it per pending action; given none (server mode, or
  any caller that didn't supply one), it rejects the action once and stops
  rather than silently approving it or hanging — surfaced as a new
  `"confirmation_required"` terminal `verification_state` that
  short-circuits task_tracker/project verification entirely. `cli.py`
  supplies a real terminal `input()`-based handler
  (`_confirm_pending_actions`) only when `cfg.confirm_mode == "always"`;
  `server.py` supplies none. See MANUAL.md "Confirmation mode" and
  "Decisions log" below. Also now enforces `HARNESS_MAX_TASK_SECONDS`, a
  shared, task-level wall-clock budget spanning every phase combined (the
  initial run and both retry loops) — `HARNESS_MAX_ITERATIONS` alone only
  bounds a single `conversation.run()` call, and each of the (up to three)
  calls per task gets its own fresh iteration budget from the SDK, so a
  task could otherwise spend roughly `HARNESS_MAX_ITERATIONS × (1 + 2 ×
  HARNESS_MAX_VERIFY_RETRIES)` iterations with no single value bounding
  the total. `_run_with_confirmation()` now returns one of three string
  outcomes (`"ok"`/`"confirmation_required"`/`"budget_exhausted"`, not a
  bool) and checks an absolute `time.monotonic()` `deadline` before every
  single `.run()` call it makes, including each confirm-mode approve/
  reject round-trip; `_verify_and_report`/`_enforce_task_tracker_completion`
  each also check it once at entry. Exhausting it produces a new
  `"budget_exhausted"` terminal `verification_state`, short-circuiting
  whichever later phases hadn't run yet — same short-circuit shape as
  `"confirmation_required"` and `"incomplete"`. See MANUAL.md "Task
  budget" and "Decisions log" below for why wall-clock was chosen over
  reverse-engineering the SDK's internal per-call iteration count. Also
  now supports `HARNESS_INTERACTIVE=yes`'s initial-run checkpoint: a new
  `OnAwaitingInput` callback (`Callable[[str], str | None]`, next to the
  pre-existing `ConfirmCallback`), consulted only around the *initial*
  `conversation.run()` (see MANUAL.md "Interactive mode" and decisions log
  for the confirmed scope) — once it reaches a normal `FINISHED` status,
  `stream_task` loops: build the narrative text produced since the last
  checkpoint (`_narrative_text()`, a small local copy of `server.py`'s
  `_narrative_texts` — kept separate since `server.py` depends on this
  module, not the reverse), call the callback, and either end the loop
  (falsy return) or `send_message()`/re-run with it. `deadline` is
  extended by the wall-clock time spent inside the callback before the
  next `_run_with_confirmation` call, so time spent waiting on a human
  reply is never charged against `HARNESS_MAX_TASK_SECONDS`. Has no effect
  without a caller-supplied callback — `cli.py` supplies one, `server.py`
  never does. Also now wires `HARNESS_MODEL_SELECTION=auto` end to end: at
  `stream_task` entry, loads the catalog, classifies the task, ranks
  candidates into a `ModelChain`, resolves an interactive override if both
  `cfg.interactive` and `on_model_choice` are given, and builds the initial
  `Agent`/`LLM` from the chosen entry's config (via
  `model_selection.config_for_entry` + `build_agent(usage_id=...)`) instead
  of `cfg` directly — every other field (`workspace`, `execution`,
  `confirm_mode`, ...) stays on the original, unmodified `cfg`. A new
  `_run_conversation_once()` wraps every `conversation.run()` call
  (threaded through `_run_with_confirmation`) in `try/except
  ConversationRunError`, falling back through the chain on a provider/
  API-level failure (see decisions log for what that actually means —
  it took two rounds of live verification to find the right exception
  type) and re-raising once exhausted. `_verify_and_report`/
  `_enforce_task_tracker_completion` also advance the same chain — one
  step per retry iteration — right before resending their own automated
  followup message. `write_model_decisions()` runs in a `finally` block
  around the whole task (not just on a normal return), so
  `MODEL_DECISIONS.md` still captures the full escalation history even
  when every candidate ultimately fails and the task raises — caught live,
  not found by inspection (see decisions log). Also wires
  `HARNESS_ARTIFACTS_DIR` (see `artifacts.py`): mints a `run_id` (or uses
  the caller-supplied one — `server.py` passes its own `TaskRecord.id`) and
  `started_at` when the feature is on, accumulates every message produced
  across the whole task (not just the interactive checkpoint's buffer,
  which gets cleared) into a full transcript, and — in the same `finally`
  block already writing `MODEL_DECISIONS.md` — extracts
  `conversation.conversation_stats.get_combined_metrics()` and
  `.usage_to_metrics` (a free per-model breakdown, since auto model
  selection already keys each candidate's `usage_id` distinctly) and calls
  `write_run_artifacts()`. A new `project: str | None` parameter on
  `stream_task`/`run_task` carries the project *name* through — `Config`
  itself has no such field (only the already-resolved `workspace` path),
  so `cli.py`/`server.py` (which both already have the name before
  resolving it) pass it again here.
- `custom_tools/run_tests_tool.py` — a language-neutral verification
  pipeline in four explicit, independently-tested stages: **project
  detection** (`detect_project()`, one marker-file walk covering Python/
  Node/Go/Rust/Java-Maven/Java-Gradle, falling back to Python for bare
  source with no manifest, else `"unknown"`), **verification-plan
  discovery** (`discover_verification_plan()`, pure — decides which
  commands apply per language, never inventing one a project doesn't
  configure), **command execution** (`execute_check()`, the only
  subprocess-spawning stage, returns one of five `CheckOutcome.status`
  values: `passed`/`failed`/`skipped`/`unavailable`/`timed_out`), and
  **aggregation** (`VerificationRun`, whose `.state` property is
  `verified`/`failed`/`inconclusive`/`timed_out` — a secondary check can
  only ever pull `verified` down to `failed`, never promote it). The
  agent-facing `run_tests` tool (unchanged contract: always exactly one
  check) keeps its original rich Python/Node behavior and reuses stages 1–3
  for Go/Rust/Java. `run_full_verification()` orchestrates all four stages
  for the harness's own post-hoc loop. Registers at import time (not just
  on demand) so both `local` execution and the Docker image's
  `--import-modules` mechanism pick it up. `CheckSpec` also supports a
  `precomputed: CheckOutcome | None` field for a pure, no-subprocess static
  check (`execute_check` returns it directly) — first used by
  `entrypoint-ordering` (see the matching Known-limitations/decisions-log
  entries): an `ast`-based check that a Python entry-point file's own
  `if __name__ == "__main__":` guard is its last top-level statement,
  catching a bug class a passing pytest suite structurally cannot (a name
  referenced from inside the guard that isn't defined until later in the
  file — invisible on `import`, real when the script is run directly).
  `_python_plan()` also now runs each detected entry point directly
  (`python <file>`, stdin closed via `execute_check`'s
  `stdin=subprocess.DEVNULL` — now the default for every check, not just
  this one, closing a latent hang risk). See "Decisions log" below for why
  this generalization was scoped this way, and for what's genuinely
  verified vs. only detected per language. `detect_project()` also now
  recognizes and reports monorepo ambiguity (`language="ambiguous"`,
  `.candidates` listing every `(language, root)` pair found) instead of
  silently picking one candidate project by directory-walk order — see the
  matching decisions-log entry.
- `cli.py` — `python -m harness "<task>" [--execution] [--project] [--agents-md]
  [--model] [--api-key] [--base-url] [--reasoning-effort] [--interactive]
  [--require-verification] [--acceptance-checks]`. `task` is resolved via
  `resolve_task_source()`: http(s) URL (fetched) or an existing local file
  (read) take precedence over literal text. CLI-only — server mode's `task`
  field does not do this resolution. `--require-verification` (off by
  default) makes an `inconclusive` verification result exit nonzero too,
  matched on the server side by `TaskRequest.require_verification`
  (`POST /tasks` flips `status` from `"completed"` to `"failed"` for that
  one case; `WS /tasks/stream` sends a `"type": "error"` frame instead of
  `"result"`) — every other `verification_state` is unaffected either way.
  See MANUAL.md "CLI reference" and "Server mode" and decisions log below
  for why this is opt-in rather than a flipped default.
  `resolve_acceptance_checks()` resolves `--acceptance-checks` the same
  "existing file wins over literal text" way `resolve_task_source()`
  resolves `task` (minus the URL-fetch case), parses the JSON via
  `acceptance.parse_acceptance_checks()`, and reports any error as a
  `Configuration error: ...` before `run_task` is ever called.
  `--interactive` (off by default) sets `cfg.interactive=True` and passes
  `_prompt_for_continuation` (an `input()`-based terminal handler,
  mirroring `_confirm_pending_actions`'s self-contained print+prompt shape)
  as `run_task`'s `on_awaiting_input` — the CLI-only half of
  `runner.py`'s interactive checkpoint; `server.py` never supplies one.
  `--auto-model` (off by default) sets `cfg.model_selection="auto"` and
  passes `_prompt_for_model_choice` as `on_model_choice`, but only when
  `--interactive` is *also* set — without it, auto-selection still runs,
  just fully automatically (no prompt). See the matching `model_catalog.py`/
  `model_selection.py`/`runner.py` entries below and MANUAL.md "Automatic
  model selection".
- `model_catalog.py` — pure data/parsing/scoring for `models.yaml`, no SDK
  imports: `load_model_catalog(path)` parses and validates the file
  (`ModelCatalogEntry` per model: `name`/`model`/`api_key`/`base_url`/
  `reasoning_effort` mirroring `.env`'s `LLM_*` fields, plus `description`,
  open-ended `ratings`, and `activated` — bool, default `true`);
  `classify_task(text, catalog)` matches task text against `task_profiles`'
  keyword `triggers` (same deterministic, no-extra-LLM-call pattern as
  skills' `KeywordTrigger`, first match wins, `default` profile as
  fallback); `score_entry`/`rank_candidates` compute a weighted sum over
  whatever axes a profile's `weights` declares (not hardcoded to any fixed
  axis set) and sort descending, skipping any entry with `activated: false`
  entirely (never scored, never a fallback candidate) — raises if that
  leaves nothing activated. No minimum catalog size enforced — a 1-entry
  catalog just has no fallback chain.
- `model_selection.py` — the SDK-touching orchestration layer only
  `runner.py` calls: `ModelChain` is a forward-only cursor over one task's
  ranked candidates (computed once, per `rank_candidates`' docstring, never
  re-scored), with `.advance()` logging a `ModelDecisionRecord` (never
  carries `api_key`/`base_url`, only names/scores/reasons — always safe to
  write/log) and returning `None` once exhausted;
  `config_for_entry()`/`llm_for_entry()` reuse `config.override_llm()` +
  `llm.build_llm()` exactly as they already exist (passing `""`, not
  `None`, for a field the entry leaves unset, so a keyless local model
  can't silently inherit a stale API key from a different provider — see
  decisions log); `write_model_decisions()` (re)writes `MODEL_DECISIONS.md`
  in the project workspace from the full decisions list every time it's
  called, never appending, so it can't duplicate its own header.
- `artifacts.py` — pure file-writing for `HARNESS_ARTIFACTS_DIR` (blank =
  disabled): `resolve_run_artifacts_dir(artifacts_dir, project, run_id)`
  builds `<artifacts_dir>/<project-or-"_unscoped">/<run_id>`, guarded by
  the same path-containment check `config.resolve_project_dir()` already
  applies to `HARNESS_PROJECTS_DIR` (a separate implementation, not a
  shared call, since the error text needs to name the right env var —
  same "different root, different error context" call already made for
  `acceptance.py`'s containment check); `write_run_artifacts()` writes
  `metadata.json`/`transcript.json`/`metrics.json` into it. No SDK
  dependency — `runner.py` extracts everything (messages, metrics,
  outcome) into plain data first. A separate, sibling top-level directory
  (mirroring `HARNESS_PROJECTS_DIR`'s own shape), not nested inside any
  one project — keeps harness telemetry out of a project's own
  (possibly-committed) tree. `MODEL_DECISIONS.md` is unaffected — it stays
  exactly where it already was; this is for genuinely new data only.
- `workspace.py` — single dispatch point for execution backends
  (`build_workspace(cfg)`); `local` returns a plain path, `docker` returns a
  `DockerWorkspace`, both as context managers so cleanup is automatic.
- `docker/agent-server.Dockerfile` — layers our tool source onto
  `ghcr.io/openhands/agent-server`, built automatically on first use.
- `server.py` — `GET /health`, `POST /tasks` (async, returns immediately),
  `GET /tasks/{id}` (poll status/partial progress/result), `WS /tasks/stream`
  (live streaming), `GET /v1/models` + `POST /v1/chat/completions`
  (OpenAI-compatible adapter, streaming and non-streaming). All task-facing
  endpoints share one background thread + callback pattern. `POST /tasks` /
  `WS /tasks/stream` accept optional `model`/`api_key`/`base_url`/
  `reasoning_effort` overrides (via `config.override_llm`);
  `/v1/chat/completions` accepts the same thing under `llm_model`/
  `llm_api_key`/`llm_base_url`/`llm_reasoning_effort` — kept distinct from
  its wire-mandated `model` field, which stays echo-only (see decisions log).
  `POST /tasks` / `WS /tasks/stream` also accept an optional
  `require_verification` field (default `false`), the server-side
  counterpart to `cli.py`'s `--require-verification` — deliberately not
  added to `/v1/chat/completions`, which doesn't surface `verification_state`
  at all (see the matching backlog entry). Both also accept an optional
  `acceptance_checks` field (a JSON array, same shape as CLI
  `--acceptance-checks`, always inline — no file-path resolution
  server-side); `_TaskRecord.acceptance_results` and the WS `"result"`
  frame both expose `TaskOutcome.acceptance_results` (`asdict`-serialized)
  regardless of whether anything was downgraded, same "always visible, not
  just on override" treatment as `completion_contract`. Also not added to
  `/v1/chat/completions`, for the same reason. Also adds `DELETE
  /tasks/{task_id}` (delete one record, `404` if unknown) and `DELETE
  /tasks?project=NAME` (delete every record for a project, `{"deleted":
  <count>}`, `0` for an unknown project is a normal result) — the REST half
  of the task-store persistence feature below.
- `task_store.py` — pluggable persistence for `server.py`'s task registry,
  selected by `HARNESS_TASK_STORE` (`memory` default | `redis` | `sqlite` |
  `mysql` | `postgres`), every backend implementing one `TaskStore` ABC
  (`save`/`get`/`delete`/`delete_by_project`/`purge_expired`/`close`) so
  `server.py`'s request-handling code never needs to know which is active.
  `memory` is byte-for-byte the pre-existing dict-based behavior.
  `sqlite`/`mysql`/`postgres` share one `SQLTaskStore` implementation via
  SQLAlchemy Core, parameterized only by connection URL (`_build_sql_url`).
  `redis`'s `RedisTaskStore` uses Redis's own native per-key expiration
  (`EX` at write time for a terminal-status record) instead of a purge
  sweep. `HARNESS_TASK_TTL_SECONDS` (default `0` = keep forever) governs
  retention for every backend; `server.py` runs a 60s background sweep
  calling `store.purge_expired()` only when a TTL is actually configured
  (a no-op for `redis`, which already expires natively). `build_task_store(cfg)`
  is the one factory both `server.py` and `admin_cli.py` call — adding a
  future backend is one new branch there, not a change to either caller.
  See MANUAL.md "Task registry persistence" for the user-facing writeup and
  the decisions log below for why SQLAlchemy Core (not three hand-written
  backends) and why REST + a CLI (not just one) for delete-by-project.
- `admin_cli.py` (`harness-admin` console script) — a maintenance CLI
  talking directly to `build_task_store(load_config())`, no running server
  required: `show <id>`, `delete-task <id>`, `delete-project <NAME>`,
  `purge [--ttl-seconds N]` (refuses to run at `ttl_seconds <= 0` rather
  than silently no-op'ing or, worse, being one accidental flag away from
  "delete everything"). The CLI half of the "Both" decision below.
- `skills.py` — two mechanisms, don't conflate them: `load_skill_catalog()`
  loads the shared, reusable, trigger-based catalog (`skills/`, arbitrary
  subfolders for classification) into every agent's `AgentContext`
  (`agent.py`); `write_project_context()` writes a caller-supplied,
  project-specific `AGENTS.md` (CLI `--agents-md`, REST `agents_md`, both
  requiring `--project`/`project`). See MANUAL.md "Skills" for the full
  writeup and CLAUDE.md-linked rationale. The catalog now includes eight
  authored, legacy-format (`triggers:`/`paths:`) lifecycle skills under
  `skills/lifecycle/` — `repository-discovery`, `requirements-analysis`,
  `implementation-planning`, `testing-and-verification`,
  `debugging-and-failure-repair`, `security-review`,
  `documentation-and-operational-readiness`,
  `completion-and-release-readiness` — language/framework-independent,
  keyword/path-triggered (not `SKILL.md`/`invoke_skill`-based); these
  supersede an earlier five-skill `SKILL.md` set downloaded from
  `addyosmani/agent-skills` (see "Decisions log" below for why).

## Backlog — optional / not yet built

- **ECS/EC2 execution backends** — `workspace.py`'s `build_workspace()` is the
  single dispatch point; adding one is a new branch there plus a new
  `HARNESS_EXECUTION` value, not a rewrite. Nothing beyond `docker` exists.
- **`run_tests`/verification generalization — now covers six languages,
  still not fully generic.** Was Python(pytest)/Node(`npm run build`)-only;
  now detects and verifies Python, Node, Go, Rust, and Java
  (Maven/Gradle) — see "Decisions log" and MANUAL.md "Custom tools" for the
  full per-language table. This covers the concrete failures that
  motivated it (a JSX parse error pytest-only verification couldn't see;
  "no tests found" being silently treated as proof of correctness) and the
  explicit follow-up ask (many languages, not just two). **Still not
  done/genuinely out of scope:** a JS project's actual test runner
  (jest/vitest/etc.) isn't config-parsed — only a defined `scripts.test` is
  run as opaque; no other Python lint/typecheck tool (flake8, pylint,
  pyright) is detected, only ruff/mypy; no JS/TS lint tool (ESLint, etc.,
  see the matching backlog entry below) is detected; no ecosystem beyond
  these six (Ruby, PHP, .NET, C/C++, ...); Java verification is detection-
  only in this project's own dev/CI environment and the Docker
  agent-server image, since neither ships a Maven/Gradle toolchain (see
  MANUAL.md "Known limitations" for the precise verified-vs-detected
  breakdown). Detecting an arbitrary project's real test runner and
  installing its deps remains a real scope question, not a quick fix.
- **Milestone 3 live provider-swap proof** — blocked on a second provider key
  or local model endpoint (see table above).
- **JS/TS lint tools (ESLint, etc.) are not auto-detected from config** —
  a Node project's own `scripts.lint`/`scripts.typecheck` *are* now run if
  defined (added alongside the Go/Rust/Java generalization), but there's no
  equivalent of Python's `_ruff_configured()`/`_mypy_configured()` that
  infers lint/typecheck is relevant from an ESLint/tsconfig file when no
  `package.json` script names it. Same "real scope question" as the
  generalization item above, not a quick fix (ESLint config formats and
  flat-config variants make "is lint configured" a harder question than the
  ruff/mypy case).
- **The OpenAI-compatible `/v1/chat/completions` adapter doesn't surface
  `verification_state`.** The native REST/WS API (`GET /tasks/{id}`, `WS
  /tasks/stream`) exposes it; the OpenAI-shaped endpoint's wire format has
  no natural field for it and wasn't touched when this was added elsewhere
  (see the matching decisions-log entry) — a caller using only that adapter
  gets the agent's own final text with no independent verification signal.

## Known limitations (internal/architectural — see MANUAL.md for user-facing ones)

- **`HARNESS_MAX_ITERATIONS` was parsed/validated but never actually wired to
  anything — silently a no-op since Milestone 1, contrary to spec section 12's
  acceptance criterion ("`HARNESS_MAX_ITERATIONS` reliably bounds runaway
  loops").** Found while adding `runner.py`'s test-verification retry loop:
  `Conversation(...)` accepts `max_iteration_per_run` (SDK default `500`),
  which `runner.py` never passed, so every run silently used `500`
  regardless of `.env`. Fixed in the same change by passing
  `max_iteration_per_run=cfg.max_iterations`. No test previously caught this
  because no test asserted on iteration-capping behavior at all.
  **Closed:** `tests/test_runner.py::test_stream_task_wires_max_iterations_to_conversation`
  now monkeypatches `runner.Conversation`/`build_agent`/`build_workspace`
  with recording fakes and asserts `stream_task()` actually calls
  `Conversation(max_iteration_per_run=cfg.max_iterations, ...)` — no
  LLM/network call, so it always runs (unlike the e2e smoke test, which
  skips without an API key). Verified it actually catches this exact class
  of regression by temporarily reverting the `max_iteration_per_run=...`
  line and confirming the test fails with `KeyError:
  'max_iteration_per_run'`, then restoring it and re-running the full suite
  (241 passed).
- Spec section 7's original custom-tool template didn't match the installed
  SDK (v1.47.0): `ToolDefinition` is subclassed with a `create(cls,
  conv_state, **params)` classmethod and an auto-derived `name` ClassVar, not
  built via a standalone factory returning `ToolDefinition(name=..., ...)`
  instances. Corrected once (`custom_tools/example_tool.py`,
  `run_tests_tool.py`) — re-verify against live docs/examples on any SDK
  upgrade, don't assume the corrected pattern still holds.
- `get_default_tools()` lives at `openhands.tools.preset.default`, not
  re-exported from `openhands.tools.preset` as spec section 6 assumed — same
  "re-verify on SDK upgrade" risk as above.
- `file_editor` requires absolute paths and does not resolve a relative one
  against the workspace directory itself.
- `python-dotenv` does not strip a trailing `# comment` from a line whose
  value is otherwise blank (a value-then-comment line strips fine).
- `sys.executable` inside a PyInstaller-frozen process (our Docker image)
  resolves to the frozen binary itself, not a Python interpreter — worked
  around in `run_tests_tool.py` (`_python_command()`); worth remembering for
  any *future* subprocess-spawning custom tool meant to run under `docker`
  execution.
- **A `Message`'s `role` does not indicate where its human-readable text
  lives.** An `assistant`-role message that makes a tool call has *empty*
  `content` (the call is in `tool_calls`); the actual text — including the
  agent's final "finish" message — comes back as a `tool`-role message's
  content instead. Confirmed by dumping a real run's full message list via
  `POST /tasks`, not assumed from any docstring. First implementation of
  `server.py`'s OpenAI-compatible adapter filtered to `role == "assistant"`
  (the obvious-looking choice) and silently returned empty content on every
  real call despite the underlying task succeeding — caught only by live
  verification, not by the unit tests (which used fakes shaped by the same
  wrong assumption). Fixed by excluding only `system`/`user` roles instead.
  Any *future* code that reads `Message.role` to decide what's "the answer"
  should re-check this rather than assume `assistant` is where output lives.
- **A plain-text LLM reply — including a clarifying question or a premature
  "done" summary — ends the run exactly like calling `finish`, with no
  error.** Root cause, confirmed by reading `response_dispatch.py`'s
  `_handle_content_response` directly: the SDK sets `execution_status =
  FINISHED` whenever the LLM's response has text and no tool call, logged
  internally as "awaits user input" — indistinguishable from an actual
  `finish` tool call to `runner.py`'s `conversation.run()`, which this
  harness calls exactly once with nobody able to answer a follow-up.
  Confirmed live **twice**, two distinct symptoms of the same cause: (1)
  given a real, substantial multi-step task, the agent planned it, invoked a
  skill, then replied "Let me know if you have specific preferences!" with
  no file written; (2) after fixing (1), a later run on the same task built
  1 of its own 6 `task_tracker` items (a demo component), then declared
  success and reframed the other 5 — booking logic, backend, responsiveness,
  testing — as "next steps for you". Fixed by giving every agent an
  `AgentContext.system_message_suffix` (`agent.py`'s `_AUTONOMOUS_SUFFIX`)
  instructing it to never wait for confirmation, never leave its own
  `task_tracker` list incomplete, never reframe required-but-unbuilt
  functionality as an optional suggestion, and only stop via `finish` once
  work is actually complete — see decision entry below. Not something a unit
  test can catch (needs a real LLM call, and the prompt steering is a soft
  instruction, not an SDK-enforced constraint) — **status: fixes for symptoms
  (1) and (2) applied but not yet re-verified live** (see backlog:
  harness-side task_tracker-completion enforcement, considered and deferred
  in favor of trying the prompt fix first). `HARNESS_INTERACTIVE=yes` (see
  "What's implemented" above) is the opt-in escape hatch for a human running
  the CLI: it deliberately drops `_AUTONOMOUS_SUFFIX` and, exploiting the
  exact ambiguity described here, always offers a terminal checkpoint at
  `FINISHED` rather than trying to guess which of the two this actually was.
- **A related but distinct "agent assumes a human is present" failure: it
  runs a CLI tool that prompts interactively, the prompt silently
  auto-cancels with no TTY/human to answer it (often still exit code 0, no
  actual result), and the agent doesn't notice — then just re-runs the
  identical command.** Confirmed live: asked to scaffold with Vite, the
  agent ran `npm create vite@latest kitesurfing-booking-site -- --template
  react-ts`; `create-vite` started its interactive template/linter prompt,
  which auto-cancelled (no files created) even though the command's own
  output printed the exact fix (`"To create in one go, run: create-vite
  <DIRECTORY> --no-interactive --template <TEMPLATE>"`) — the agent re-ran
  the same failing command instead of using that flag, burning 200K+ tokens
  and $0.35+ before the user killed the process by hand. Contributing
  factor, not the root cause: `HARNESS_EXECUTION=local`'s terminal falls
  back to a subprocess (no real PTY) when tmux isn't installed (see
  MANUAL.md "Known limitations"), so an interactive prompt can't be answered
  even in principle here — but the fix (use non-interactive/CI flags; don't
  blindly retry a command that visibly did nothing) is the right tool-usage
  habit regardless of PTY support, not just a workaround for this terminal.
  Fixed the same way as the two entries above: `agent.py`'s
  `_NONINTERACTIVE_TOOLING_SUFFIX`. **Status: applied, not yet re-verified
  live.**
- **The strongest variant yet: the agent explicitly called `finish` while
  admitting known test failures, and its own diagnosis of the failure was
  wrong.** Confirmed live: asked to build a Tower of Hanoi game, the agent's
  `finish` message said "there are errors in the tests that need further
  debugging, particularly around sequence handling and state transitions
  within the game logic" — a deliberate `finish` call despite a known-bad
  state, not an accidental plain-text stop like symptom (1). Checked the
  actual file: `hanoi.py` had a literal `IndentationError` (`def main():`
  left with an empty body; the real driver code had been appended to the
  bottom of `auto_solve()` instead) — the file didn't even parse, so every
  test failed at collection, not from "sequence handling and state
  transitions." The agent's summary was invented, not observed — it never
  re-ran anything before writing it. This is the first of the four symptoms
  where a *prompt-only* fix looked insufficient on its face (the model
  wasn't just failing to follow an instruction, it was confidently wrong
  about its own state), so this one got two fixes instead of one: (a)
  `agent.py`'s `_VERIFY_BEFORE_FINISH_SUFFIX` (same lever as before, still
  worth trying first per the pattern), and (b) `runner.py`'s
  `_verify_tests_and_retry()` — real, harness-side, no-LLM verification that
  doesn't depend on the model being honest or accurate about its own state.
  See the decisions log entry below for why both were built together this
  time instead of trying (a) alone first. **Status: both applied; (b) was
  additionally confirmed against the actual broken `hanoi.py` project
  (real `IndentationError`, correctly detected, exit code 2) — see the git
  history for this change. Not yet re-verified against a fresh live run of
  the full agent + verification loop together.**
- **A fifth variant of the same "confident agent, wrong self-report"
  pattern, but pytest-based verification couldn't see it at all — the
  project wasn't Python.** Confirmed live: asked to scaffold a kitesurfing
  booking site with Vite/React, the agent declared "The kite surfing booking
  website has been set up successfully and the development server is
  running," reframing booking functionality as "next steps." Launching the
  actual dev server showed `App.tsx` had a real JSX parse error (`[oxc]
  PARSE_ERROR: Adjacent JSX elements must be wrapped in an enclosing tag` at
  a `<div className="hero">`) — the page didn't render at all.
  `HARNESS_VERIFY_TESTS`'s existing `_verify_tests_and_retry()` (see above)
  ran but was blind to this: it only ever invoked pytest, which against this
  project reports "no tests collected" (exit code `5`) — correctly not a
  failure for a Python project with no tests, but a false pass here since
  there was never a pytest suite to run in the first place. Same underlying
  cause as the `hanoi.py` case above (self-report isn't verification, only
  actually running the code is) but exposes a second, narrower gap: the
  harness's *own* verification only knew how to check one ecosystem. Fixed
  by generalizing `run_tests_tool.py`/`_verify_tests_and_retry()` to detect
  a Node project with a `package.json` `build` script and run `npm run
  build` instead of pytest for it — see the matching decisions-log entry
  below for what's still deliberately out of scope (JS test runners,
  non-Python/Node ecosystems). **Status: applied; unit-tested against real
  `npm`/`node` subprocesses (success, failure with parse-error-shaped
  output, missing-npm fallback) and against the detection logic directly
  (nested `package.json`, Python-markers-win, no-build-script fallback) —
  not yet re-verified against a fresh live run reproducing the original
  kitesurfing failure end to end.**
- **A sixth variant, requested proactively rather than found live: the
  harness's own verification loop couldn't tell "confirmed working" apart
  from "found nothing to check," and had no visibility into a run that
  never reached a coherent finish at all.** Two compounding gaps, both
  addressed in the same change (see the matching decisions-log entry for
  why a bigger rework was chosen over another incremental patch): (1) a
  Node project whose primary check couldn't run at all (npm missing) was
  previously lumped in with "no tests collected" and silently treated as
  fine — not a failure, but also not something that should look
  indistinguishable from an actual clean pass; (2) `runner.py` never read
  `conversation.state.execution_status` at all, so a run the SDK's own
  stuck-loop detection (`stuck_detection=True`, on by default) ended in
  `STUCK` — or one that ended in `ERROR` — looked exactly like a normal
  finish to every caller, the same "run ends without the caller knowing it
  didn't really complete" problem as the `_AUTONOMOUS_SUFFIX` family above,
  but at the SDK level rather than a prompt-following one. Fixed by
  replacing the old binary "did tests fail" check with a five-state model
  (`verified`/`failed`/`inconclusive`/`retry_exhausted`/`stuck` — see
  MANUAL.md "Test verification") and checking `execution_status` for
  `STUCK`/`ERROR` before verifying and after every retry. **Status:
  applied; unit-tested (a fake `Conversation` whose `state.execution_status`
  is set to the real SDK's `ConversationExecutionStatus.STUCK`/`ERROR`,
  confirmed importable from `openhands.sdk` and confirmed via
  `BaseConversation`'s `state`/`execution_status` properties that this
  works identically for both `local` and `docker` execution) — not yet
  re-verified against a real run that actually gets stuck.**
- **A seventh variant, again requested proactively: every verification
  concept in this harness — detection, "no false success," retry, the
  state model — was implicitly Python/Node-only.** The prior generalization
  (variant 5 above) fixed the concrete JSX-parse-error bug but left the
  architecture itself two-language-shaped: `_detect_verification()`
  returned a hardcoded `("pytest", ...) | ("npm_build", ...)` tuple, and
  `CheckOutcome`'s `ran: bool` / `passed: bool | None` pair couldn't express
  "this should be checkable but isn't" (a missing Go/Rust/Java toolchain)
  as anything other than the same bucket as "not configured" or "no tests
  yet" — exactly the kind of silent conflation variant 5 had just fixed for
  Node, left unfixed for every other language. Rebuilt around four named
  stages (project detection / plan discovery / command execution /
  structured results — see "What's implemented" above and MANUAL.md
  "Custom tools") and a `CheckOutcome.status` enum of five values
  (`passed`/`failed`/`skipped`/`unavailable`/`timed_out`) instead of the
  two-field boolean pair, specifically so "unavailable" (a real gap) and
  "skipped" (a correct absence) can never be confused again, for any
  language. Added `timed_out` as a genuinely new terminal state (not
  present before this change at all) because a hung/looping check and a
  wrong-answer check are different problems worth telling apart — see the
  matching decisions-log entry for the full reasoning and what's honestly
  verified vs. only detected per language (Java, concretely: detected
  reliably, verified only if a Maven/Gradle toolchain happens to be
  present — neither ships in this project's own dev/CI environment or the
  Docker agent-server image today). **Status: applied; unit-tested per
  stage plus real-subprocess end-to-end runs for every language whose
  toolchain is installed in this dev environment (pytest, npm, ruff, go,
  cargo, clippy) and the real "configured but no toolchain" path for mypy
  and Maven (neither installed here, so these are real gaps being
  exercised, not simulated ones) — not yet re-verified against a live task
  that actually asks the agent to build a Go/Rust/Java project end to end.**
- **An eighth variant, and the first one where the "wrong" thing the agent
  did was defensible: a live Tower of Hanoi run had the agent repeatedly
  re-edit already-correct code because the *test* it was chasing was wrong,
  not the implementation — and its own diagnostic messages degraded into
  fluent, grammatically normal, semantically empty prose across both
  attempts.** User-reported, not synthetic. Traced the actual test by hand
  against `HanoiGame(3)`'s real state: `test_invalid_move_larger_on_smaller`
  asserts `move_disk('C', 'B')` should be invalid ("larger on smaller") at
  a point in its own move sequence where C's top disk is actually 1 (the
  smallest), not larger — moving it onto empty B is a legitimately valid
  move. The implementation was correct the whole time; the test's own
  comment was wrong about what it was testing. The agent's `<=`→`<` edit to
  `move_disk`'s comparison was a genuine no-op for this failure (that
  branch is never reached — `not self.rods[destination]` short-circuits it
  when the destination is empty, which it was), and pytest's captured
  output — including the exact `C: [2, 1]` state trace showing the top disk
  was 1 — was available to the agent on every attempt but never used to
  question the test. This is the same "trust the wrong premise instead of
  tracing the real state" family as the `hanoi.py` `IndentationError` case
  earlier in this file, but inverted: there the code was wrong and the
  agent's self-report was wrong about why; here the code was *right* and
  the test was wrong, and the harness's/skills' own "never weaken a test to
  make it pass, fix the code instead" guidance actively pointed the agent
  at the wrong side of the bug. Separately and more concerning: the agent's
  own final messages on both retries were internally coherent English with
  no concrete engagement with the actual failure at all ("Condition Energy
  Evaluation," "retro-verifying conditionally determined returns from
  contradictory game states violations circumspect inherently making sense
  of failed revisions") — the SDK's own `stuck_detection` (on by default)
  never flagged this (verified by reading `stuck_detector.py` directly: it
  only detects *exact* repetition of actions/observations/messages, and
  every retry here had a genuinely different edit and different text, so
  none of its five patterns apply). Two separate fixes, see matching
  decisions-log entries: (a) `skills/lifecycle/testing-and-verification.md`
  and `debugging-and-failure-repair.md` now explicitly tell the agent to
  prove a test is wrong against its own captured state trace before
  re-editing working code a second time; (b) `runner.py` gained a
  `no_progress` terminal state that stops the retry loop the moment a fix
  attempt provably changes nothing observable in the verification output —
  a mechanical, output-based check, deliberately not a judgment about
  whether the agent's prose still makes sense. **Status: both applied;
  (b) verified against the actual on-disk `projects/hanoi/` repro left by
  this exact live run (running `_run_verification()` against it twice
  confirms an identical failure signature, and running the full
  `_verify_and_report()` loop against it with a fake conversation confirms
  the loop now stops after 1 retry instead of 2) — (a) is prompt-level
  guidance, not yet re-verified live since it can't be mechanically tested
  the way (b) was.**
- **A ninth variant, and the cleanest example yet of a gap that was never
  the agent's fault: a fresh Tower of Hanoi generation had every unit test
  pass — genuinely, no false self-report this time — while the actual
  program crashed the moment a user picked the `SOLVE` menu option, and the
  harness still reported `Verification: verified`.** User-reported, ran the
  actual generated CLI by hand. Root cause, confirmed by reading the
  generated `hanoi.py` directly: `solve()` (the function the `SOLVE` menu
  branch calls) was defined *after* `if __name__ == "__main__": main()` —
  when the file is `import`ed (exactly what `test_hanoi.py`'s `from hanoi
  import HanoiGame, solve` does), Python runs the whole file top-to-bottom
  before any test executes, `__name__ != "__main__"` so the guarded
  `main()` call never fires, and `solve` is fully defined by the time any
  test calls it — every test passes, honestly. Running `python hanoi.py`
  directly calls `main()` immediately at that line, before the interpreter
  ever reaches the later `def solve(...)` — `NameError`, the instant `SOLVE`
  is chosen. This is qualitatively different from every prior "confident
  agent" variant in this file: the agent's self-report was accurate (it
  said tests passed, and they did), and the harness's own post-hoc pytest
  re-run (already a safety net specifically for a *dishonest* self-report)
  was equally fooled, because pytest genuinely could not see this bug — it
  only ever imports the module, the one execution path the bug is invisible
  from. No amount of "trust the agent less, re-run the tests yourself" (the
  fix for every earlier variant) touches this, since the *test itself*
  structurally cannot exercise the failing path. Fixed with two new,
  independent Python-specific checks in `run_tests_tool.py`'s secondary-check
  set (see "What's implemented" above and the matching decisions-log entry
  for the full design reasoning): a static `entrypoint-ordering` check
  (`ast`-based, catches this exact bug class by name, no subprocess) and a
  `python <entry point>` smoke-run (stdin closed, catches startup crashes
  more generally but — honestly documented as a real, deliberate limit, not
  hidden — cannot reach an interactive branch like `SOLVE` itself, so it
  did not and could not have caught this specific crash; `entrypoint-ordering`
  is what did). **Status: applied; verified against the actual on-disk
  `projects/hanoi/` repro left by this exact live run —
  `run_full_verification()` against it reports `entrypoint-ordering` failed
  with the precise diagnosis (names `solve` and `hanoi.py` directly) while
  `pytest` genuinely passes, and overall `VerificationRun.state` is
  `failed`, not `verified` — plus a synthetic-fixture regression test
  reproducing the identical bug shape (a helper function defined after the
  guard, called from inside it) and its fixed counterpart.**

## Decisions log (why, not just what)

- **Machine-checkable acceptance criteria: scoped down to `file_exists`/
  `file_contains` only, deliberately dropping the "run a command" check
  kind the original ask explicitly named.** The item's own text flagged
  this itself ("an arbitrary caller-supplied 'command' check is itself a
  command-injection-shaped surface worth scoping carefully"), so this
  wasn't discovered mid-implementation — it was the central scoping
  decision made before writing any code. The distinguishing factor from
  "the agent can already run arbitrary commands via its own terminal
  tool" (already true, already accepted): a command-type acceptance check
  would be a check the *harness itself* runs, triggered directly by
  whatever a caller puts in a request field — no agent step, no
  confirm-mode gate (`HARNESS_CONFIRM_MODE=always`, see the matching
  decisions-log entry above, has no bearing on harness-triggered
  execution), no review of any kind in between. Combined with server
  mode's standing lack of authentication (`todo.md`'s server-auth item),
  a command kind would hand an unauthenticated network caller direct code
  execution on the host — a strictly worse, and new, surface compared to
  what already exists. A caller-supplied file *path* check doesn't have
  this problem once properly contained (see below), so `file_exists`/
  `file_contains` were kept and command execution was dropped rather than
  attempting to "sandbox" it — there's no sandboxing primitive already
  available in this harness that would make a caller-controlled `exec`
  actually safe over an unauthenticated network endpoint.
  - **Path containment reuses `config.resolve_project_dir`'s exact logic
    (reject absolute/`..` outright, then a symlink-followed containment
    check) but as a separate implementation in `acceptance.py`, not a
    shared function.** Considered extracting a shared
    `resolve_contained_path()` primitive — rejected: the two callers have
    different roots (a task's workspace here, `HARNESS_PROJECTS_DIR`
    there) and, more concretely, `resolve_project_dir` already has
    passing tests asserting on its exact error-message wording (`"Project
    name must be relative..."`, `"...escapes HARNESS_PROJECTS_DIR"`);
    parameterizing the wording to serve both callers risked either
    breaking those assertions or producing a generic message worse for
    both. A ~15-line near-duplicate, clearly cross-referenced in both
    docstrings, was judged cheaper and lower-risk than refactoring
    already-verified, security-critical code — matches this project's own
    stated "three similar lines is better than a premature abstraction"
    guidance (`CLAUDE.md`).
  - **A read-size cap (`_MAX_FILE_READ_BYTES = 1_000_000`) for
    `file_contains`, not an unbounded read.** A "does this file contain
    X" check has no legitimate reason to need more than a modest prefix
    of a file; without a cap, a caller (again, potentially unauthenticated
    over the network) could point a check at an arbitrarily large file
    and impose real memory/CPU cost on the harness process for every
    request. Truncation is reported in the result's `detail` text rather
    than silently applied, so a check that fails because the match falls
    outside the read window is visibly different from one that fails
    because the text is genuinely absent.
  - **A new `"acceptance_failed"` terminal state, applied once at the very
    end of `stream_task` via `_apply_acceptance_checks`, wrapping
    whichever `TaskOutcome` came back from any of the three phases —
    not evaluated inside `_verify_and_report` itself, and not retried.**
    Matches the item's literal ask ("make a failed required criterion
    block a verified result") precisely: the override only ever fires
    when the *existing* logic would have reported `"verified"`, since
    every other terminal state already represents some other failure with
    nothing left to "block". Deliberately does not trigger a retry cycle
    the way a failing test does — the item didn't ask for that, and
    bolting acceptance-check failures onto the existing test-retry loop
    (which is keyed on `VerificationRun`/`CheckOutcome`, a different
    vocabulary than `AcceptanceCheckResult`) would have been a much larger
    change for a request that only asked for detection and blocking, not
    automated remediation. `acceptance_results` is still attached and
    `acceptance_criteria`/`limitations` still extended even when nothing
    is downgraded (or when the underlying state already wasn't
    `"verified"`), so a caller always sees exactly what was checked.
  - **Not added to `/v1/chat/completions`**, for the same reason
    `require_verification` wasn't: that adapter's wire format has no
    field for `verification_state` at all, so a flag whose only effect is
    changing that value would have no observable effect through it.
- **Global task execution budget: a wall-clock deadline
  (`HARNESS_MAX_TASK_SECONDS`), not a reverse-engineered cumulative
  iteration counter.** The gap itself was concrete and already precisely
  quantified before this change: `runner.py` calls `conversation.run()` up
  to three times per task (the initial run, then up to
  `HARNESS_MAX_VERIFY_RETRIES` more in each of the two retry loops), and
  `local_conversation.py`'s own `run()` method resets its `iteration`
  counter to `0` on every single call — confirmed by reading the SDK
  source directly, not assumed — so `HARNESS_MAX_ITERATIONS` alone cannot
  bound how many total iterations one task consumes across all three call
  sites (`HARNESS_MAX_ITERATIONS × (1 + 2 × HARNESS_MAX_VERIFY_RETRIES)`
  in the worst case). The item's own wording explicitly accepted "an
  iteration, wall-clock, or equivalent task-level budget" as satisfying
  the ask, which mattered here: the two options are not equally cheap or
  equally safe to build.
  - **Why not iteration-count accounting.** The SDK exposes no API to read
    back how many iterations a completed `.run()` call actually consumed
    — only whether it hit its own `max_iteration_per_run` cap. The only
    way to reconstruct a count would be inferring "one iteration ≈ one new
    `ActionEvent`" from `conversation.state.events`, verified live to be
    *approximately* true (every tool call, including `finish` itself,
    produces exactly one `ActionEvent`) but confirmed **not exact**: this
    file's own Known Limitations already documents a live case where the
    agent's final turn is plain text with no tool call at all (the "a
    plain-text reply ends the run exactly like `finish`" entry) — that
    iteration produces no `ActionEvent`, so an event-counting approach
    would systematically undercount by one in exactly the ambiguous case
    this project has already been burned by once. Reimplementing the
    SDK's own internal step-counting logic in harness code is also a
    direct SDK-drift risk (golden rule 2): it works today by inference
    from event shapes the SDK never contractually promised, and could
    silently break on an upgrade with no test able to catch it short of a
    live run.
  - **Why wall-clock instead.** Trivial to implement correctly
    (`time.monotonic()` before/after, no SDK internals involved, zero
    drift risk), and arguably closer to what a user actually cares about
    for a runaway-task safety valve — "how long is this going to run" —
    than an exact step count, which even a correct implementation
    wouldn't translate into a cost or time guarantee anyway (a fast cheap
    iteration and a slow expensive one both count as "1"). A separate,
    genuine $-cost ceiling is tracked separately (`todo.md`'s "Per-task
    cost ceiling" item) rather than folded into this one — iteration
    count, wall-clock time, and dollar cost are three different
    quantities, and conflating them would have made this change do more
    than the one thing it was asked to do.
  - **Single choke point: `_run_with_confirmation()` checks the deadline,
    not three separate checks at each call site.** That function already
    wrapped every `conversation.run()` call in `runner.py` (added for
    `HARNESS_CONFIRM_MODE=always`) — extending it to also check
    `deadline` before each call, and changing its return type from `bool`
    to a three-way string (`"ok"`/`"confirmation_required"`/
    `"budget_exhausted"`), closed the gap in one place rather than
    duplicating a `time.monotonic() >= deadline` check at three call
    sites. A shared `_outcome_for_run_result()` helper builds the right
    `TaskOutcome` for either non-`"ok"` result, so `stream_task`,
    `_enforce_task_tracker_completion`, and `_verify_and_report` each only
    need a two-line branch instead of their own copy of the contract-
    selection logic.
  - **Checked before *every* `.run()` call inside `_run_with_confirmation`,
    including confirm-mode retries — not just once per phase.** A long
    approve/reject back-and-forth under `HARNESS_CONFIRM_MODE=always` is
    itself an unbounded sequence of `.run()` calls (bounded only by how
    many actions a human is willing to sit through) — checking the
    deadline only at function entry would have left exactly the kind of
    unbounded-retry gap this whole feature exists to close, just moved
    one level down.
  - **`_verify_and_report`/`_enforce_task_tracker_completion` also each
    check the deadline once at entry**, in addition to relying on
    `_run_with_confirmation`'s per-call check inside their retry loops —
    matches the established "check at entry and after every retry"
    pattern already used for `_ABORTED_STATUSES` in both functions, so a
    budget that expired during a slow `_run_verification()` subprocess
    call (which has its own internal timeout, not tied to this budget) is
    still caught promptly on the next phase rather than silently ignored.
  - **Default `1800` seconds (30 minutes), enabled by default, not an
    opt-in `0`/`None` = unlimited.** Unlike `--require-verification`
    (a genuine behavior-changing default fork the user was asked about
    directly), this is a pure safety backstop in the same spirit as
    `HARNESS_MAX_ITERATIONS`'s own always-on default (`50`) — CLAUDE.md's
    golden rule 6 ("harness code must enforce iteration limits... wherever
    possible") applies directly, and a generous default value carries
    negligible risk of tripping during normal use while still bounding the
    genuinely pathological case this item was written to catch.
  - **Existing internal test call sites for `_run_with_confirmation`,
    `_verify_and_report`, and `_enforce_task_tracker_completion` were kept
    working unchanged** by giving `deadline` a default of `float("inf")`
    (and keeping `on_confirm`'s pre-existing `None` default) at the
    function level, even though the one real production caller
    (`stream_task`) always computes and passes a real deadline from
    `cfg.max_task_seconds`. Avoided rewriting roughly two dozen pre-existing,
    already-reviewed test call sites for a parameter irrelevant to what
    they were actually testing — the four call sites that specifically
    test `_run_with_confirmation`'s return value did still need updating,
    since its return type itself changed from `bool` to a string.
  - Verified live end-to-end, not just via unit tests: a real task with
    `HARNESS_MAX_TASK_SECONDS=1` completed its actual work (created a
    file) within its single `.run()` call, then correctly reported
    `budget_exhausted` once control returned to `runner.py`'s post-run
    checks, with exit code `1` — and a real task under the default budget
    behaved identically to before this change. Also caught, live, a real
    omission this same verification pass would have missed if skipped:
    `cli.py`'s nonzero-exit tuple was not updated for the new
    `"budget_exhausted"` state on the first pass, confirmed by a live run
    reporting `budget_exhausted` with exit code `0`, then fixed and
    re-verified (exit `1`) — a dedicated regression test for exactly this
    was added and confirmed to fail against the unfixed code before being
    left in place passing.
- **`--require-verification`/`require_verification`: opt-in flag with the
  default kept exactly as-is, not a flipped default with an opt-out.**
  This item was raised as a genuine open question — the CLI's own code
  comment already said "isn't an error — nothing was proven broken" as a
  deliberate justification for exiting `0` on `inconclusive`, so this
  wasn't a bug to silently fix. Presented the user three shapes (flip the
  default with an opt-out flag for today's behavior, matching the item's
  own proposed phrasing; keep the default and add an opt-in flag; leave it
  alone entirely) rather than picking one, specifically because flipping a
  CLI exit code's default is a real backward-compatibility break for any
  existing script or CI pipeline that already depends on `inconclusive`
  exiting `0` — not a decision to make unilaterally on the user's behalf.
  Chosen: keep the default (`inconclusive` still exits `0`), add
  `--require-verification` as a new, purely additive flag. Zero risk to
  any existing caller; a caller that wants a stricter guarantee opts in
  explicitly.
  - **Threaded through both CLI and the native REST/WS server API, not
    CLI-only.** The item's own phrasing ("keeping the state itself... and
    server responses") called for parity, and the underlying ambiguity
    (`status`/exit-code alone can't distinguish "verified" from "nothing
    could be checked") is identical in both surfaces — a server caller
    that only checks `status` has exactly the same blind spot a CLI script
    checking only the exit code does.
  - **Deliberately not added to `/v1/chat/completions`.** That adapter
    already doesn't surface `verification_state` at all (a standing,
    documented gap — see the matching backlog entry) — adding a flag whose
    entire purpose is "change what happens for one specific
    `verification_state` value" to an endpoint that never exposes that
    value in the first place would be a flag with no observable effect a
    caller could rely on.
  - **REST: flips `status` from `"completed"` to `"failed"` (with `error`
    explaining why), not a new third `status` value.** Every existing
    poller already branches on exactly two terminal values
    (`"completed"`/`"failed"`) per `_TaskRecord`'s own docstring — reusing
    that existing contract means a caller who adopts `require_verification`
    needs zero new code to detect the failure, only to opt into the flag.
  - **WS: sends a `{"type": "error", ...}` frame instead of `{"type":
    "result", ...}`, for the identical reason** — `"error"` was already a
    real frame type every client must already handle, so this needed no
    new frame type either.
  - **`require_verification` only ever special-cases `"inconclusive"`,
    never any other `verification_state`.** Every other state already has
    well-defined, independently-decided pass/fail semantics from earlier
    work in this file (`retry_exhausted`/`no_progress`/`timed_out`/
    `incomplete`/`confirmation_required`/`stuck` are already nonzero/
    `"failed"` unconditionally; `verified` is already the only real
    success) — this flag's entire scope is the one state that was
    previously being treated as "not a failure" by design, and widening
    its scope to touch other states was never asked for and would blur a
    single-purpose flag into something broader.
- **Project path containment (`resolve_project_dir`): a real security bug,
  not just missing hardening, fixed with one shared resolver in
  `config.py` rather than a fix duplicated in `cli.py` and `server.py`.**
  Both `cli.py`'s `--project` and `server.py`'s `project` request field
  built the workspace path the same unguarded way:
  `os.path.abspath(os.path.join(cfg.projects_dir, project))`. Confirmed by
  reading Python's own `os.path.join` semantics that this is exploitable,
  not theoretical: `os.path.join(a, b)` silently *discards* `a` when `b`
  is absolute, so `--project /etc/cron.d` (or the identical field in an
  unauthenticated `POST /tasks` request — see the server-mode-no-auth
  item, `todo.md` #5) redirected the agent's entire workspace, including
  its terminal and file-editor tools, to an arbitrary absolute path on the
  host. `..` traversal (`--project ../../etc`) had the same effect through
  ordinary path normalization, no absolute path needed.
  - **Placed in `config.py`, not a new module.** It's a small (~30-line),
    single function tightly coupled to `Config.projects_dir` and to
    `ConfigError` — both callers already import `config.py` and already
    catch `ConfigError` at every call site that resolves a project
    (`cli.py`'s existing try/except pattern around `load_config`/
    `override_llm`; `server.py`'s three `except (ConfigError, ValueError)`
    blocks around `_resolve_cfg`), so raising `ConfigError` from inside
    `resolve_project_dir` needed zero new exception-handling wiring at
    either call site.
  - **String-level rejection first (absolute path, `..` segment, empty/
    `.`/`..` name), then a resolved-path containment check
    (`os.path.realpath` on both sides, `Path.is_relative_to`) as a second,
    independent layer — not just one or the other.** The string check
    gives a precise, actionable error message for the obvious attack
    shapes; the resolved-path check catches what the string check
    structurally cannot: a project name with no `..` and no absolute
    component at all (e.g. `myapp`) whose target already exists as a
    symlink pointing outside `projects_dir`. Verified live with a real
    symlink created via `tmp_path` in the test suite (not simulated) that
    the resolved-path check catches this, and that an ordinary symlink
    which stays *inside* `projects_dir` is correctly still allowed —
    a symlink itself is not the violation, only one that escapes is.
  - **Deliberately does not defend against a TOCTOU race** (a symlink
    swapped in at the validated path between the containment check and
    `os.makedirs`). Out of scope for this fix: this harness's actual
    threat model doesn't involve an adversary racing filesystem operations
    against the same project directory mid-request, and closing that gap
    properly (e.g. `O_NOFOLLOW`-based directory creation) is disproportionate
    engineering effort for a risk with no realistic exploitation path here.
- **`HARNESS_CONFIRM_MODE=always` wiring: a `_run_with_confirmation()`
  wrapper around every `conversation.run()` call, an explicit
  `on_confirm` callback threaded through `stream_task`/`run_task`, and a
  new `"confirmation_required"` terminal state for the no-handler case —
  not a silent auto-approve and not a CLI-only special case.** This item
  had been deferred since it was first identified specifically because it
  needed `/verify-sdk` first — done here by reading
  `openhands/sdk/security/confirmation_policy.py` and
  `local_conversation.py`'s `run()` loop directly, then confirming the
  actual mechanics live (not just from source): `AlwaysConfirm` pauses
  before every tool call (`execution_status` becomes
  `WAITING_FOR_CONFIRMATION`); `ConversationState.get_unmatched_actions
  (conversation.state.active_branch())` returns the pending `ActionEvent`s;
  calling `conversation.run()` again approves and executes them;
  `conversation.reject_pending_actions(reason)` rejects them (status drops
  to `IDLE`) and a further `run()` lets the agent react to the rejection.
  Reproduced both paths against a real conversation before writing any
  harness code.
  - **Wrapped every `conversation.run()` call site, not just the initial
    one.** `runner.py` already had three: the initial run, and one each
    inside `_verify_and_report`'s and `_enforce_task_tracker_completion`'s
    retry loops. All three can trigger a fresh confirmation pause (the
    agent might propose a risky action while trying to fix a failing
    test, not only on its very first move), so `_run_with_confirmation()`
    replaces the raw call at all three sites rather than only guarding the
    first one and leaving the retry loops to break on an unhandled
    `WAITING_FOR_CONFIRMATION` status.
  - **No `on_confirm` handler present (e.g. server mode) rejects the
    pending action exactly once and stops, rather than silently
    approving it or looping.** Silently approving would defeat the entire
    point of `HARNESS_CONFIRM_MODE=always` — a user who set it explicitly
    asked for a real gate, not a no-op with different wiring. Looping
    (re-checking forever, hoping someone eventually answers) has no
    natural end condition since nothing is polling for an answer.
    Rejecting once and returning a new terminal state
    (`"confirmation_required"`) matches the existing `"stuck"` precedent
    in this file: an explicit, visible "this needs manual attention,"
    not a run that silently vanishes or hangs.
  - **A dedicated `"confirmation_required"` state, not folded into
    `"stuck"`.** The conversation didn't get stuck or error in the SDK's
    own sense — the run stopped because of a harness-level policy
    decision (no one available to answer), a different and more specific
    cause worth telling apart, matching this file's established pattern
    of one state per distinct cause (see the `incomplete`/`no_progress`/
    `timed_out` entries above).
  - **`cli.py` supplies a real terminal handler
    (`_confirm_pending_actions`, `input()`-based, rejecting on anything
    other than an explicit `y`/`yes`), `server.py` supplies none** —
    consistent with this being, in practice, a CLI-only feature (there is
    no terminal to prompt at in server mode); a server deployment that
    sets `HARNESS_CONFIRM_MODE=always` will see every task immediately
    end in `confirmation_required` on its first tool call, which is
    documented in MANUAL.md as the expected (if not very useful) behavior
    rather than an unexplained failure.
  - **Known, accepted limitation, not fixed here:** each resumed
    `conversation.run()` call gets its own fresh `max_iteration_per_run`
    budget from the SDK (confirmed by reading `local_conversation.py`:
    `iteration` is a local variable reset to `0` every call). A long
    interactive approve/reject session adds a further call site to the
    same "no single shared budget across multiple `run()` calls" gap the
    verify/task-tracker retry loops already have — see `todo.md`'s "Add a
    global task execution budget" item. Not addressed in this change; the
    no-handler path stops after exactly one rejection specifically so it
    can't compound that risk on its own.
- **Harness-side `task_tracker`-completion enforcement: read live
  conversation events directly, not the tool's `TASKS.json` persistence
  file — and a new `"incomplete"` `verification_state` that short-circuits
  project verification, not a fold into an existing state.** This backlog
  item was originally scoped as "inspect the tracker after
  `conversation.run()` and auto-resend 'continue — N items remain'" — but
  before implementing, read `openhands/tools/task_tracker/definition.py`
  directly (not assumed) and found the tool's state actually lives in the
  `TaskTrackerExecutor` instance's `self._task_list`, optionally mirrored
  to `save_dir/TASKS.json` only when `conv_state.persistence_dir` is set —
  and confirmed by reading `local_conversation.py` that `runner.py`'s
  `Conversation(...)` call never passes `persistence_dir`, so that file is
  never written in this harness's normal operation (`ConversationState`'s
  own log line confirms it falls back to an in-memory store). Relying on
  the file would have meant either always enabling persistence (a bigger,
  unrelated behavior change) or silently no-op'ing in the harness's actual
  default configuration. Instead reads `conversation.state.events`
  directly — confirmed live (a real `Conversation` run, not assumed) that
  it holds every `ObservationEvent` for the conversation's lifetime
  regardless of persistence settings, and that a task_tracker call's
  `ObservationEvent.tool_name` equals `TaskTrackerTool.name` (looked up
  from the class, not hardcoded as the string `"task_tracker"`, in case a
  future SDK version renames it) — so `_task_tracker_snapshot()` just scans
  for the most recent one. Confirmed the whole mechanism against a live
  repro built specifically to reproduce the target failure mode: asked the
  agent to "create two tasks, mark one done, leave the other todo, then
  finish" — it did exactly that and called finish anyway, the harness's new
  check caught the pending item, sent a follow-up, and the agent then
  actually completed it before finishing for real.
  - **A dedicated `"incomplete"` terminal state, not folded into
    `retry_exhausted`.** An unresolved task list is a different claim than
    "a test kept failing": it means the harness never established
    completion in the first place, so running project verification
    afterward would be checking correctness of a task the agent's own
    tracking says isn't finished — potentially reporting `verified` for
    the one piece that got built while five others silently didn't (the
    exact "1 of 6 items, declared success" failure this item was written
    to close). `_enforce_task_tracker_completion` therefore runs *before*
    `_verify_and_report` in `stream_task` and returns a terminal
    `TaskOutcome` (short-circuiting verification entirely) rather than
    letting an incomplete tracker be just another input into the existing
    test-retry loop.
  - **Reused `cfg.max_verify_retries` as the retry budget, not a new
    `HARNESS_MAX_TASK_TRACKER_RETRIES` knob.** Both loops answer the same
    underlying question — "how many automatic fix-then-recheck cycles are
    we willing to spend before giving up and telling the caller manual
    attention is needed" — and the original backlog note's own concern
    ("a runaway 'continue' loop is a real risk... needs its own
    stop-condition") is fully addressed by bounding on the existing,
    already-battle-tested budget rather than inventing a second one a user
    would have to separately understand and tune.
  - **A tracker that was never used, or is already fully `done`, is not a
    violation and is not reported at all** — matches the tool's own
    description ("For single straightforward tasks, proceed with direct
    implementation rather than creating tracking overhead"); treating an
    unused tracker as incomplete work would punish the agent for correctly
    judging a task too trivial to need it.

- **Pinned `openhands-sdk`/`openhands-tools` to `==1.47.0` exactly, not a
  floating/caret range.** Both were unpinned since Milestone 1, which meant
  a plain `uv pip install -e .` could silently pull a newer SDK release at
  any time — and this file's own "SDK-drift warning" (`CLAUDE.md`) already
  documents two symbols (`get_default_tools()`'s import path, the
  custom-tool `Action`/`Observation`/`Executor` pattern) that changed
  between what the spec assumed and what the installed SDK actually
  required. Checked the currently-installed version first
  (`importlib.metadata.version(...)`, both `1.47.0`) rather than guessing —
  it matches the version already named throughout this file's SDK-drift and
  decisions-log entries (`ConversationExecutionStatus`, the `create(cls,
  conv_state, **params)` tool pattern, etc.), so pinning to it locks in
  exactly the API surface every prior verification in this file was actually
  checked against. An exact pin (`==`), not a range, is deliberate: this
  project's whole "verify-first" posture (`CLAUDE.md` golden rule 2) is
  about not trusting SDK symbols we haven't re-checked, and a range would
  let a minor/patch bump silently reintroduce that exact risk. Bumping the
  version is now a deliberate, visible edit to `pyproject.toml` followed by
  `/verify-sdk`, not something that happens as a side effect of routine
  dependency installation. Verified against the full test suite
  post-pin (`uv pip install -e ".[dev]"` re-resolves cleanly, `uv run pytest
  -q` — 240 passed) rather than assumed safe.
- **Docker execution — custom image, not `DockerDevWorkspace`.** The SDK's
  `DockerDevWorkspace` (on-the-fly image builds) is explicitly scoped to the
  SDK's own dev environment, not external projects. Built
  `docker/agent-server.Dockerfile` instead: layers our tool source + an
  `ENTRYPOINT` override (`--import-modules harness.custom_tools`) onto the
  published `ghcr.io/openhands/agent-server` image. A raw pass-through to the
  stock image doesn't work — `register_tool()` only affects the process that
  calls it, and the container runs its own separate agent-server process.
- **Server mode — async submit+poll *and* WebSocket streaming, not just one.**
  Prompted directly by "what if code generation takes too long" — a single
  blocking `POST /tasks` forces every client to hold a connection open for an
  unbounded duration. Kept both because they solve different problems: poll
  when you don't need live updates, stream when you do. Both share the same
  background-thread-plus-callback pattern (`stream_task`'s `on_message` hook)
  rather than being two separate implementations.
- **Three-document split: README vs MANUAL.md vs ROADMAP.md.** README is a
  short quickstart pointing elsewhere. `MANUAL.md` is the full user-facing
  operational reference (setup, config, CLI, execution modes, troubleshooting).
  This file (`ROADMAP.md`) is internal planning memory — status, backlog,
  decisions — not meant for end users. Keeping these separate (rather than one
  large doc) avoids a casual user wading through backlog/decision-log content
  to find "how do I run this," and avoids re-deriving project history from
  conversation scrollback in future sessions.
- **Skills: two separate mechanisms, not one "smart" one.** Considered (a) the
  caller passing an AGENTS.md-equivalent with every task, (b) an LLM call to
  pick the "best" skill for a task, (c) a shared skill repository (git or
  local). Rejected (b): the SDK's own `KeywordTrigger`/`TaskTrigger`/
  `PathTrigger` matching (confirmed live — see `agent_context.py`'s
  `get_user_message_suffix`, which runs automatically on every user message)
  already decides relevance deterministically, for free, with no extra LLM
  call or custom classifier. Kept (a) and (c) as genuinely different, both
  needed: (c) is reusable expertise (this repo's `skills/` — local-dir-backed
  today via `load_skills_from_dir`, but `MarketplaceRegistration`/
  `load_public_skills` already exist in the SDK for a git-repo-backed catalog
  if that's ever wanted); (a) is a persistent fact about *one* project
  (`--agents-md`/`agents_md`, written by the harness into the project's
  `AGENTS.md` — never by a human, since no human touches a `--project`
  subfolder between task invocations in this harness's actual usage model).
- **`AgentContext.load_project_skills=True` is required, and non-obviously
  so.** `AgentContext`'s own `_load_auto_skills` validator explicitly does
  *not* handle `load_project_skills` (it hardcodes `include_project=False`) —
  that flag is instead consumed later, lazily, by `LocalConversation` once
  the real workspace path is known (`conv_state.workspace.working_dir`).
  Confirmed by reading `local_conversation.py` directly rather than assuming
  from the field's docstring. Without setting this flag explicitly on the
  `AgentContext` we build, `write_project_context()`'s `AGENTS.md` would be
  written but silently never loaded.
- **CLI `task` argument stays a single positional, with content-sniffing —
  not separate `--task-file`/`--task-url` flags, and not scoped to the
  server too.** A URL (http/https only) or an existing local file wins over
  literal text; no override flag to force literal interpretation, since a
  task description colliding with a real, existing filename is an unlikely
  edge case not worth a second flag for. Deliberately CLI-only: a human
  typing a command line benefits from not needing `--task-file`/`$(cat ...)`
  shell tricks; a REST/WS API caller is already writing code and can
  read/fetch content itself before the request — adding the same resolution
  server-side would just be a second, redundant place doing the same thing.
- **OpenAI-compatible endpoint mounted on the same `server.py`/`create_app()`,
  not a separate app or module.** One running process, multiple protocol
  surfaces — matches how most "agent gateway" tools are actually deployed,
  and reuses `_resolve_cfg`/the background-thread-plus-queue pattern already
  built for `/tasks` and `/tasks/stream` instead of a parallel
  implementation. `model` in the request is accepted and echoed back (wire
  format requires the field) but never used to select a provider — consistent
  with the model-agnostic invariant holding through every interface, not
  just the native one.
- **Per-request LLM override (`--model`/`--api-key`/`--base-url` and the
  server equivalents) added as an explicit, separate mechanism — not by
  repurposing `/v1/chat/completions`'s `model` field.** Motivation: let a
  caller run the same task against different providers/models to compare
  results, without editing `.env` (still the default/no-override behavior —
  this is additive, not a replacement for the model-agnostic invariant).
  Considered making the OpenAI-compatible endpoint's `model` field actually
  select the provider once explicitly overridden — rejected: that field is
  wire-mandated and a strict OpenAI client may put an arbitrary non-LiteLLM
  string there (`"gpt-4o"` with no prefix), so trusting it to double as a
  real override risks either breaking normal clients or silently ignoring
  the override depending on how it's parsed. Added `llm_model`/
  `llm_api_key`/`llm_base_url` as separate extension fields on
  `ChatCompletionRequest` instead — `model` keeps its prior "echoed only"
  contract unchanged, matching the decision entry directly above. `TaskRequest`
  (no OpenAI wire-format constraint) uses the plain `model`/`api_key`/
  `base_url` names directly. Both routes end in the one new
  `config.override_llm()` helper, called from `cli.py` and `server.py`'s
  `_resolve_cfg`, so the override logic itself isn't duplicated.
- **Streaming narrates every non-echo message, not just the final answer.**
  Considered emitting only the last message once the run finishes (simpler,
  but defeats the point of "streaming" for a multi-minute agent run) versus
  streaming every `assistant`/`tool`-role message as it arrives (chosen) —
  gives real-time progress matching what the CLI's visualizer and
  `WS /tasks/stream` already show, at the cost of a chat UI seeing tool
  output narrated inline rather than one clean final message. The
  non-streaming path uses the same `_narrative_texts()` concatenation for
  consistency between the two modes.
- **Steer the agent to work autonomously via `AgentContext.system_message_suffix`,
  not via `runner.py`'s control flow.** Once the "plain text ends the run"
  behavior above was root-caused, considered having `runner.py` detect a
  FINISHED-without-`finish`-tool-call ending and auto-resend a "please
  continue, no user is available" message — rejected: it treats a symptom
  (the run stopped) without addressing why the agent chose to stop
  (uncertainty/wanting confirmation), so it would likely just loop into
  another clarifying question, and it complicates the runner's otherwise
  simple send-once/run-once shape for every future reader. Instead told the
  agent, once, in the system prompt: there's no user to ask, proceed on your
  own judgment, only stop via `finish`. Cheaper, addresses the actual cause,
  and costs nothing when the agent was never going to ask a question anyway.
- **Per-project `README.md` requirement — a system-prompt instruction
  (`_README_SUFFIX`), not a harness-generated template file.** User request:
  every generated project should explain how to run it. Considered having
  the harness itself write a starter `README.md` into the project directory
  (e.g. alongside `write_project_context`'s `AGENTS.md`) — rejected: the
  harness has no idea what the agent is about to build (a static site? a
  FastAPI service? a CLI?), so it can't write real install/run instructions
  up front, only a placeholder the agent would then have to notice and
  rewrite anyway. Telling the agent — the only party that actually knows
  what it built — to write the README itself as part of finishing the task
  is simpler and produces accurate instructions. Trade-off accepted: like
  `_AUTONOMOUS_SUFFIX`, this is a soft instruction the model can skip; there
  is no harness-side check that a README actually got written (see MANUAL.md
  "Projects" → "README.md").
- **Interactive-CLI-hang fix — another `system_message_suffix` policy
  (`_NONINTERACTIVE_TOOLING_SUFFIX`), not a switch to a PTY-backed terminal
  or harness-level command retry-limiting.** Considered installing tmux (the
  SDK's own terminal already warns it would use a more stable PTY-backed
  session instead of the subprocess fallback) — deferred from the fix
  itself: a real PTY would let an interactive prompt render, but the agent
  would still need to *send* the right keystrokes to answer it, which is a
  worse outcome than avoiding the prompt entirely via a non-interactive
  flag, so tmux alone doesn't fix the actual problem. Investigated
  separately, live: `docker` execution already ships tmux in the published
  `ghcr.io/openhands/agent-server` base image (`tmux -V` → `3.5a` as the
  container's non-root `openhands` user) — no Dockerfile change needed. Only
  `local` execution was missing it, so installed it on the dev machine via
  `brew install tmux` (`3.7c`) — worth doing for terminal stability
  generally, orthogonal to the prompt-level fix above. Considered detecting repeated-identical
  terminal commands in `runner.py` and refusing/warning on a repeat —
  rejected for the same reason the task_tracker-completion loop was deferred
  above: it treats the symptom (retrying) without the cause (not recognizing
  the command produced nothing), and adds a new stop-condition to design
  carefully. Prompt-level guidance is the same low-cost lever already used
  for the two entries above, applied to a third variant of the same
  "no human is actually present" theme.
- **Harness-side test verification (`runner.py`'s `_verify_tests_and_retry`)
  built together with its matching prompt fix, not deferred to "try the
  cheap fix first" like the three symptoms above.** The established pattern
  in this file — try `_AUTONOMOUS_SUFFIX`-style prompt steering, only build
  harness-side enforcement if that proves insufficient on a live re-test —
  was deliberately broken here because this symptom is qualitatively
  different: it isn't the model failing to follow an instruction (a
  behavior prompt steering can plausibly fix), it's the model being
  *factually wrong* about its own state (claiming plausible-sounding but
  invented reasons for a test failure it never re-checked). A prompt saying
  "verify before you claim something" cannot make a model's self-assessment
  accurate — only actually running the code can, and the harness can do that
  itself without depending on the model at all. `run_tests_tool`'s executor
  already existed and was directly reusable for this (see "What's
  implemented" above), which also made the cost of building this immediately
  low rather than a from-scratch design. Reused the SDK's own
  `send_message()`-then-`run()` re-entry pattern (confirmed live/via source
  that `send_message()` resets a `FINISHED`/`STUCK` `execution_status` back
  to `IDLE`, so the same `Conversation` object can be driven through
  multiple verify-fix cycles) rather than starting a fresh `Conversation`
  per retry, which would have discarded all prior context (files already
  read, decisions already made) and made each retry redo work from scratch.
  Bounded by `HARNESS_MAX_VERIFY_RETRIES` (default `2`) for the same reason
  the task_tracker-completion loop above was flagged as needing a stop
  condition — a project whose tests are simply wrong, or a bug genuinely
  beyond the model's ability to fix, must not loop forever.
- **Five SDLC skills (`requirements-analysis`, `implementation-planning`,
  `testing-and-verification`, `security-review`, `release-readiness`)
  downloaded from `addyosmani/agent-skills`, not authored, per explicit user
  instruction ("download those skills, not invent them").** No package
  anywhere defines exactly these five names with the user's exact trigger
  wording — checked `anthropics/skills` (source of this repo's existing
  `frontend-design`/`webapp-testing`/`web-artifacts-builder`, no SDLC
  category at all), `sethdford/claude-skills/sdlc` (different names:
  `definition-of-done`, `release-checklist`, ...), and a GitHub repo/code
  search for the five names together (no hit). Asked the user to pick a
  source rather than approximate one; they chose `addyosmani/agent-skills`.
  Mapped by content fit against the user's own trigger descriptions, not
  1:1 by category label: `requirements-analysis` ← `spec-driven-development`
  (not `idea-refine` — its "starting a new project... requirements are
  unclear, ambiguous, or only exist as a vague idea" is a near-verbatim
  match for "feature requests or vague product tasks"); `implementation-planning`
  ← `planning-and-task-breakdown`; `testing-and-verification` ←
  `test-driven-development` (not `debugging-and-error-recovery` or
  `browser-testing-with-devtools` — TDD's own trigger, "implementing any
  logic, fixing any bug, or changing any behavior," matches "triggered by
  implementation tasks" more directly than either, and it's the
  ecosystem-agnostic one, matching this harness's own
  not-specialized-to-one-language scope); `security-review` ←
  `security-and-hardening`; `release-readiness` ← `shipping-and-launch`.
  Each downloaded `SKILL.md` was renamed (frontmatter `name:` + directory)
  to match, required because `Skill._load_agentskills_skill`'s strict-mode
  validation rejects a frontmatter `name` that doesn't match its parent
  directory name (confirmed by reading
  `openhands/sdk/skills/skill.py`/`utils.py` directly, not assumed) — so
  this isn't optional cosmetic renaming, the skill fails to load without it.
  Cross-references inside the downloaded content to sibling skills that
  *are* part of this five (`planning-and-task-breakdown` →
  `implementation-planning`, `test-driven-development` →
  `testing-and-verification`) were updated to match; references to sibling
  skills or `../../references/*.md` files from the source repo that were
  *not* downloaded (`incremental-implementation`, `context-engineering`,
  `browser-testing-with-devtools`, `observability-and-instrumentation`,
  `debugging-and-error-recovery`, `definition-of-done.md`,
  `security-checklist.md`, `testing-patterns.md`, etc.) were rewritten
  in-place to stop promising a local file that doesn't exist in this
  catalog, rather than left as dangling paths or silently deleted — each
  downloaded `SKILL.md` carries a header comment naming its exact source
  skill and what was changed. Verified live (no LLM call needed): loaded
  the full catalog via `load_skill_catalog('skills')` after adding these —
  all 12 skills (7 pre-existing + 5 new) parse without a
  `SkillValidationError`.
  **Superseded:** a later request specified precise per-skill content and
  trigger requirements for a broader 8-skill lifecycle set with names that
  overlap these five — see the "Lifecycle skills" decisions-log entry below
  for why the whole set was rewritten (authored, not downloaded) and moved
  to `skills/lifecycle/` in legacy `triggers:` format rather than kept as
  `SKILL.md`. This entry is left in place as the historical record of why
  the original five were sourced the way they were; it no longer describes
  what's on disk.
- **`run_tests` generalization — chose "detect pytest vs. Node-build" over
  either a no-op or a full test-runner-detection rewrite.** Motivated by a
  live false pass: a Vite/React project with a real JSX parse error in
  `App.tsx` was declared "set up successfully" by the agent, and the
  existing pytest-only `HARNESS_VERIFY_TESTS` safety net didn't catch it
  either — it just saw "no tests collected" (exit 5) and moved on, since
  that's the correct read for an actual Python project with no tests yet.
  Considered leaving this as a known limitation (it already was one,
  explicitly flagged in MANUAL.md and the backlog above) — rejected because
  the user hit the exact failure the limitation predicted, with a concrete
  repro in hand. Considered the "detect the project's real test runner"
  full rewrite this file's backlog entry has flagged as unscoped since
  Milestone 4 — rejected for this pass too: that requires per-ecosystem
  runner detection (jest vs. vitest vs. mocha, whether deps are installed,
  how each reports machine-parseable results) with no single project in
  hand to verify most of it against, i.e. exactly the "real scope question"
  the backlog entry already named. `npm run build` is different in kind,
  not scope: nearly every scaffolded JS/TS frontend project (Vite, Next.js,
  CRA, ...) defines a `build` script, it requires no test-framework-specific
  parsing, and — critically — it's the exact mechanism that would have
  caught this specific bug class (a file that doesn't compile/parse), which
  a project's *test* runner might not even exercise if the broken file
  isn't covered by a test. Detection deliberately defaults to pytest when
  neither a Python marker nor a `package.json` build script is found (not a
  new "none" default) to avoid changing behavior for every one of this
  repo's own pre-existing tests, which run pytest against a bare directory
  of test files with no `pyproject.toml` of their own.
- **`_SDLC_SKILLS_SUFFIX` added, not a CLAUDE.md edit or task-text framing,
  to get the five new SDLC skills actually used.** User asked how to make
  the harness's generated-app agent use the five skills added above;
  proposed two options themselves (a CLAUDE.md statement, or wrapping the
  task in a bigger pre-defined context). Investigated the SDK directly
  before answering either way (`context/prompts/sections/dynamic.py`,
  `agent/base.py`, `tool/builtins/invoke_skill.py`) rather than guessing:
  confirmed every `SKILL.md`-format skill (the five new ones plus the three
  pre-existing `frontend-design`/`webapp-testing`/`web-artifacts-builder`)
  is already listed, unconditionally, in an `<available_skills>` name +
  description menu injected into every run's prompt, with an `invoke_skill`
  tool auto-attached — discoverability was never the gap, and neither
  proposed option would have moved it: **CLAUDE.md is never read by
  `build_agent()`/`Agent(...)` at all** (it only governs this session,
  developing the harness's own source — confirmed by grepping this repo's
  own `src/` for any read of it, there is none), and the skill menu is
  injected regardless of task phrasing, so richer task text wouldn't
  increase discoverability either. The actual gap is reliability: the model
  can see a skill in the menu and still not invoke it at the point it
  applies. Fixed with a fifth `system_message_suffix` policy (same lever as
  the four pre-existing ones in this file), naming each skill and the
  lifecycle point it belongs at. Verified against the SDK source, not
  assumed, that `invoke_skill(name="<skill-name>")` (the exact call syntax
  used in the suffix's wording) is what the SDK's own prompt tells the
  model to call. Unlike the other four `_SUFFIX` policies, this one is
  proactive — added because the mechanism makes it possible, not because a
  live run already demonstrated the agent ignoring these five skills; worth
  a live re-check once a real task exercises it, same as the other
  proactively-applied fixes in this file.
  **Superseded:** `_SDLC_SKILLS_SUFFIX` was replaced by
  `_LIFECYCLE_SKILLS_SUFFIX` — see the "Lifecycle skills" entry below. The
  `invoke_skill(name="...")` mechanism this entry verified is still real
  and still used by the three `anthropics/skills`-sourced `SKILL.md` skills
  (`frontend-design`/`webapp-testing`/`web-artifacts-builder`), just no
  longer by the lifecycle set.
- **Verification loop rework: a five-state model + `CompletionContract` +
  `TaskOutcome`/`TaskResult`, not another incremental patch to
  `_verify_tests_and_retry`.** Explicit ask: a reliable, bounded SDLC loop
  that "must not claim success merely because the agent called `finish`."
  The prior shape (`_tests_are_failing(RunTestsObservation) -> bool` plus a
  give-up-notice-on-exhaustion special case) could already distinguish
  pass/fail/retry-exhausted for a *single* check, but had two structural
  gaps a bigger rework was the right size for, not another special case
  bolted on: (a) it had nowhere to put "inconclusive" as a state distinct
  from "verified" — a Node project with npm missing and a Python project
  with zero tests collected both just fell through to "didn't fail," which
  is exactly the "no tests found being treated as proof of correctness"
  failure mode the task explicitly called out to avoid; (b) it only ever
  looked at one check, so adding lint/typecheck/npm-test (a separate,
  explicit ask) meant either running them outside the state machine
  entirely (defeating the point) or reworking the state machine to
  aggregate multiple checks anyway. Chose:
  - `CheckOutcome`/`VerificationRun` (`run_tests_tool.py`) as the
    per-check/aggregate result shape, kept separate from the agent-facing
    `RunTestsObservation` so the tool's existing single-check contract
    (and its tests) stay untouched — `VerificationRun.state` is the
    aggregation rule in one place: any real failure (primary or secondary)
    forces `"failed"`; only the *primary* check passing promotes to
    `"verified"`; everything else is `"inconclusive"`. A secondary check
    (lint/typecheck) can never promote inconclusive to verified on its own
    — tidiness isn't proof the software works.
  - `TaskOutcome`/`CompletionContract` (`runner.py`) as the harness-level,
    caller-facing verdict — deterministic and harness-computed, explicitly
    *not* another LLM call or a bigger prompt (the task's own constraint:
    "do not add a large always-on SDLC prompt"). The completion contract's
    `acceptance_criteria`/`verification_checks`/`limitations` are derived
    mechanically from what actually ran, not asked of the model.
  - `TaskResult(list)` — `run_task`'s return value subclasses `list` rather
    than becoming a new type, specifically so `messages[-1]`, `len(...)`,
    `if messages:`, and iteration keep working for every existing caller
    (`cli.py`, `server.py`'s OpenAI adapter, both test suites) with zero
    changes, while `.outcome` carries the new structured verdict. Considered
    changing `run_task`'s return type outright — rejected: every caller
    would need updating in lockstep for no benefit `TaskResult` doesn't
    already give for free.
  - `"failed"` deliberately never appears as a final `verification_state` —
    it's the transient signal inside the retry loop between "a check just
    failed" and "was it fixed, or did retries run out." Keeping it as a
    real, independently-tested state on `VerificationRun` (not just an
    internal boolean) means the retry loop's own logic
    (`while run.state == "failed":`) reads as what it is.
  - CLI/server surfacing (`cli.py`'s `Verification: <state>` line +
    nonzero exit on `retry_exhausted`/`stuck`; `server.py`'s
    `verification_state`/`completion_contract` on `_TaskRecord` and the
    WS `"result"` event) was treated as in-scope, not a follow-up: the
    task's own point 10 ("make terminal outcomes explicit to callers ...
    A task with unresolved verification failures must be visibly
    unsuccessful or incomplete") is unmet if the richer outcome only exists
    inside `runner.py` and no caller ever sees it. The OpenAI-compatible
    `/v1/chat/completions` adapter was deliberately left alone (see
    backlog) — its wire format has no natural field for this and the task
    didn't ask for a wire-format extension.
  - Lint/typecheck/npm-test are opt-in by project configuration, never
    invented: `ruff`/`mypy` only run if the project's own `pyproject.toml`/
    config files declare them, `npm test` only runs against a real,
    non-placeholder `scripts.test`. A configured tool that isn't installed
    is a `limitations` entry, not a failure — an environment gap (missing
    `mypy`) isn't the same claim as a code defect, and treating it as a
    hard failure would block completion on something the agent usually
    can't fix (it doesn't control what's preinstalled in `docker`
    execution's image).
  Verified against the SDK directly before relying on it, not assumed:
  `conversation.state.execution_status` and the `ConversationExecutionStatus`
  enum (`IDLE`/`RUNNING`/`PAUSED`/`WAITING_FOR_CONFIRMATION`/`FINISHED`/
  `ERROR`/`STUCK`/`DELETING`) by reading `openhands/sdk/conversation/state.py`
  and `base.py` directly, confirming `state`/`execution_status` are declared
  on `BaseConversation` itself (so this works identically for `Conversation`'s
  `LocalConversation` and `RemoteConversation` — i.e. both `local` and
  `docker` execution) and that `ConversationExecutionStatus` is re-exported
  from `openhands.sdk` top-level.
- **Language-neutral verification: four named stages + a `CheckOutcome`
  status enum, not another per-language if/else branch bolted onto
  `run_full_verification()`.** Explicit ask: extend the verification loop
  to Go, Rust, and Java (Maven+Gradle) alongside Python/Node, "where
  project metadata makes the commands unambiguous," with unknown projects
  producing an explicit "unavailable" result rather than a guess. Before
  writing any language-specific code, read the existing
  `_detect_verification()` and confirmed it hardcoded exactly two outcomes
  (`"pytest"` or `"npm_build"`, both tupled with a `run_dir`) — adding a
  third would mean a third hardcoded tuple shape, a fourth a fourth, and so
  on; not a "generalization," a linear pile of special cases. Chose to name
  and separate the four stages the task asked for explicitly (detection /
  plan discovery / execution / structured results) as real functions with
  real unit tests per stage, rather than one longer function, because the
  three failure classes this needs to distinguish — "we don't know what
  this project is," "we know, but the tool to check it isn't installed,"
  "we tried and it failed" — map onto exactly stages 1/2/3 and are much
  easier to get right (and to test in isolation) when each is a separate
  function than when they're interleaved inside one big conditional.
  - **`CheckOutcome.status` (five-value string) replaces the previous
    `ran: bool` + `passed: bool | None` pair.** The old pair could not
    express "this should be checkable but isn't" (a missing Go/Rust/Java
    toolchain) as distinct from "not configured here" (no ruff config) or
    "ran, nothing to check" (pytest, zero tests) — all three collapsed to
    `ran=False, passed=None`. Once every language's *primary* check can
    plausibly be blocked by a missing toolchain (Go/Rust: `go`/`cargo` not
    on `PATH`; Java: `mvn`/`gradle` not on `PATH`; unknown project: no
    command at all), conflating "real gap" with "correct absence" stops
    being a rare edge case and becomes the common case for three of six
    languages — worth a real enum, not another special-cased boolean
    combination.
  - **`timed_out` added as a genuinely new terminal state**, both on
    `CheckOutcome` and (via `VerificationRun.state`, propagated by
    `runner.py`) as a `TaskOutcome.verification_state` value. Considered
    folding a timeout into the existing "unavailable" bucket (the check
    also "couldn't be completed") — rejected: a timeout usually means the
    *agent's own code* is hanging (an infinite loop, an unbounded wait),
    which is fixable by the agent the same way a real failure is, unlike a
    missing system toolchain, which isn't. So a timeout is retried the same
    as a failure (`_RETRIABLE_STATES = ("failed", "timed_out")` in
    `runner.py`), with real partial output captured from
    `subprocess.TimeoutExpired.stdout/stderr` and sent back — but if it's
    *still* timing out after the retry budget, the terminal state stays
    `timed_out` rather than becoming `retry_exhausted`, so a caller can
    tell "the fix attempts produced a real error we gave up on" apart from
    "this kept hanging the whole time," which usually point at different
    remediations (fix the bug vs. investigate the environment/timeout
    value).
  - **Per-language scoping followed the task's own qualifier — "where
    project metadata makes the commands unambiguous."** Go (`go test
    ./...`, `go vet ./...`) and Rust (`cargo test`, `cargo check`, `cargo
    clippy`) are unconditional once their manifest is found: these are
    *the* standard commands for those ecosystems, no per-project
    configuration needed, unlike Python lint/typecheck (many possible
    tools) or Node scripts (arbitrary names). Java got the same "test
    command, prefer the wrapper" treatment (`./mvnw test` / `./gradlew
    test`, falling back to a global `mvn`/`gradle`) but deliberately no
    secondary checks (no direct request for a Java lint/typecheck
    equivalent, and Maven/Gradle don't have one standard tool the way
    ruff/mypy or clippy do).
  - **Java is honestly detection-only in this project's own environment,
    documented as such rather than glossed over.** Neither `mvn` nor
    `gradle` (nor their wrappers) are installed in this dev environment or
    the Docker agent-server base image — confirmed live (`which mvn` /
    `which gradle` both fail here). Detection and plan discovery are fully
    real and tested (marker files, wrapper-vs-global resolution); the
    "verified" path was proven with a fake wrapper script that exits 0
    rather than a real Maven build, and the "unavailable" path was proven
    against this environment's *actual* absence of Maven, not a simulated
    one. MANUAL.md's "Known limitations" states this distinction plainly
    (detected vs. verified) rather than letting "Java support" imply more
    than it delivers today.
  - **The agent-facing `run_tests` tool reuses stages 1–3 for Go/Rust/Java
    instead of gaining its own second detection path.** Its Python/Node
    behavior (pytest's rich pass/fail-count parsing and `path` targeting;
    `npm run build`'s exit-status shape) stays byte-for-byte the same
    contract it always had — those code paths and their tests were left
    untouched. For every other language it now runs the plan's primary
    `CheckSpec` via the same `execute_check()` the harness's own loop uses,
    avoiding a second, divergent implementation of "what command applies
    here" that could drift from the harness-side answer.
  - **Node gained `lint`/`typecheck` secondary checks** (reading
    `scripts.lint` / `scripts.typecheck` / `scripts["type-check"]`) as part
    of this same change, per the task's explicit "configured test, build,
    lint, and typecheck scripts" — previously only `build`/`test` were
    read. ESLint/tsconfig-based *inference* (deciding lint applies even
    with no named script) was not added — see the matching backlog entry —
    since unlike Python's `[tool.ruff]`, there's no one canonical config
    location to check.
- **Lifecycle skills: legacy `triggers:`/`paths:` format under
  `skills/lifecycle/`, authored fresh, replacing the earlier five-skill
  `SKILL.md`/`invoke_skill` set — not another round of downloading.**
  Explicit ask: eight named skills (repository-discovery,
  requirements-analysis, implementation-planning, testing-and-verification,
  debugging-and-failure-repair, security-review,
  documentation-and-operational-readiness, completion-and-release-readiness)
  with precise per-skill trigger conditions and content requirements
  ("Keep each skill short and actionable," "Use precise triggers and/or
  path triggers," "Do not create one huge always-active SDLC prompt"), four
  of which share a name with a skill already on disk from the earlier
  `addyosmani/agent-skills` download.
  - **Format: legacy `triggers:`/`paths:`, not `SKILL.md`.** Read
    `openhands/sdk/skills/skill.py` directly (not assumed) before choosing:
    confirmed only the legacy format supports a real `KeywordTrigger`/
    `PathTrigger` at all — `SKILL.md` has no trigger frontmatter, full stop,
    it's always menu-listed with `invoke_skill` as the only access path
    (`_load_agentskills_skill` never reads `triggers:`/`paths:`). This
    directly satisfies "precise triggers and/or path triggers" as a literal
    requirement, not just a style preference, and gives real, testable,
    code-enforced firing instead of depending on the model choosing to
    call `invoke_skill` — the same reliability gap the now-superseded
    `_SDLC_SKILLS_SUFFIX` existed to paper over. A second, non-obvious
    finding from the same source read: a legacy skill *with* a trigger is
    **also** listed in `<available_skills>` (confirmed via the `Skill`
    docstring: "With triggers: Listed in `<available_skills>`, content
    injected on trigger") — so switching format cost nothing on the "make
    skill names/descriptions menu-discoverable" requirement, it only
    added real triggering on top.
  - **Content authored fresh, not downloaded, despite the standing
    "download real content, don't invent it" instruction from the earlier
    skill-sourcing task.** That instruction applies to *generic reusable
    engineering expertise* (TDD, OWASP threat modeling) that legitimately
    exists as someone else's published work. This task's skills are not
    that: several sentences are direct statements about *this harness's own
    architecture* — "do not rely on CLAUDE.md... use the shared skill
    catalog and the existing AGENTS.md mechanism," references to this
    project's own `repository-discovery`/`debugging-and-failure-repair`
    sibling skills by name, "distinguish 'no tests found' from 'verified'"
    (this harness's own `run_tests_tool`/`TaskOutcome` vocabulary). No
    external, generically-sourced skill could state these facts correctly;
    writing them is describing this repository's own conventions, the same
    category `commit-conventions.md`/`fastapi-conventions.md` already fall
    into (both clearly authored for this project, never presented as
    downloaded). Separately, brevity was itself a stated requirement ("short
    and actionable") the previous downloaded set (250-530 lines each)
    structurally couldn't satisfy — that format is designed for
    progressive-disclosure reference material, not a short lifecycle nudge.
  - **`requirements-analysis`, `implementation-planning`,
    `testing-and-verification`, `security-review` keep their names; content
    fully replaced.** `release-readiness` renamed to
    `completion-and-release-readiness` (matching the task's requested name;
    keeping the old name alongside the new one would have meant two skills
    covering the same pre-finish lifecycle point). The old `SKILL.md`
    directories were deleted rather than left alongside the new files —
    confirmed live this was necessary, not cosmetic: with both present,
    `load_skill_catalog()`'s `{**repo_skills, **knowledge_skills,
    **agent_skills}` dict merge silently dropped the new legacy-format
    skill for every colliding name (the `SKILL.md`/`agentskills` entry
    always overwrote it, since that dict is merged last), so the new,
    intended content would have been invisible in the actual catalog.
    Deletion is safe and reversible: the old five were already committed
    (see "sdlc" commit) before this change, so git history keeps them
    recoverable even though they're gone from the working tree.
  - **`repository-discovery` and `documentation-and-operational-readiness`
    are genuinely new** — no prior skill covered "establish the actual
    stack/conventions before editing" or "keep docs/config-reference/API
    examples in sync," so these fill real, previously-unfilled lifecycle
    gaps rather than replacing something.
  - **`agent.py`'s suffix rewritten as `_LIFECYCLE_SKILLS_SUFFIX`, per the
    task's own four required bullets** (inspect before editing; proactively
    apply lifecycle skills; skip for trivial changes; skill guidance is
    never a substitute for verification) — shorter than the old
    `_SDLC_SKILLS_SUFFIX` since it no longer needs to name eight skills
    individually or instruct an `invoke_skill` call that these skills don't
    use; it instead tells the agent to keep applying a lifecycle skill's
    *discipline* even when a task's exact wording doesn't happen to contain
    a trigger word, since `KeywordTrigger` matching is a proxy for
    relevance, not relevance itself.
  - **Known limitation, stated plainly rather than glossed over:**
    `KeywordTrigger` matching is inherently approximate. A task that
    legitimately needs `security-review` but is phrased without any of its
    ~14 trigger words won't get it injected automatically (mitigated, not
    solved, by `_LIFECYCLE_SKILLS_SUFFIX`'s "apply the discipline even
    off-trigger" instruction — itself only a prompt-level nudge, not a
    mechanical guarantee). Conversely a broad word like `api` (shared by
    `security-review` and `documentation-and-operational-readiness`) will
    fire on tasks where it's only incidentally relevant. This is the same
    class of tradeoff `KeywordTrigger` already had for the four
    pre-existing legacy skills, not a new risk introduced here — verified
    live (not assumed) via `Skill.match_trigger()` against representative
    trigger/non-trigger task text for every one of the eight skills, plus
    an explicit test that unrelated text (a Q&A question, a creative-writing
    task) does not fire any lifecycle skill.
- **`no_progress` detection: compare structured verification output across
  retries, not the agent's own message text.** User reported a live run
  where the agent's diagnostic prose degraded into fluent-sounding nonsense
  across two retries while re-editing already-correct code (see the
  matching Known-limitations entry). Investigated a text-coherence
  heuristic first (asked the user directly, offering three designs) since
  it most directly targets what was observed — rejected in favor of
  comparing verification output instead, for reasons that held up under
  scrutiny, not just cost:
  - **No reliable, cheap way to score "does this text still make sense"
    exists in this harness.** A heuristic classifier (word-rarity, sentence
    length, grammar checks) risks false-positiving on legitimately verbose
    technical reasoning and false-negatives on more short/careful
    degenerate text, and validating it would need a corpus of
    real degenerate vs. real coherent agent output this harness doesn't
    have. An LLM-based judge adds a second model call (cost, latency, and
    its own failure modes — an LLM judging LLM output is not obviously more
    reliable) and works against the model-agnostic invariant less directly
    (a judge call still has to pick *some* model).
  - **The actual harm is measurable without reading a word of prose.** A
    "no observable change" fix attempt already contains a precise, cheap,
    structured signal: the *verification output itself* — same check, same
    exit code, same content (once timing noise is stripped) — is identical
    before and after a full retry cycle. This doesn't require judging
    whether the agent's reasoning was coherent, only whether its reasoning
    (however it read) produced any measurable effect. It also generalizes:
    it would equally catch a *perfectly coherent* agent stuck re-trying the
    same ineffective fix for a mundane reason, which a text-coherence check
    would miss entirely since coherent-but-wrong text passes any prose
    check.
  - **Comparison signature strips only wall-clock timing, nothing else.**
    Considered stripping all digits (simpler regex) — rejected: that would
    also hide a genuinely different line number or a genuinely different
    assertion value between two attempts, which is exactly the kind of
    real change this check must not mask. The narrower
    `\d+(?:\.\d+)?\s*(?:s|ms|seconds?|milliseconds?)` pattern targets only
    known duration-footer noise (confirmed live: pytest's own summary line
    differed only in its trailing `in 3.85s` vs. `in 3.83s` between the two
    real failing runs, otherwise byte-identical).
  - **Fires after exactly one no-progress retry, not two.** Given
    `HARNESS_MAX_VERIFY_RETRIES` defaults to `2`, requiring two consecutive
    no-progress retries before stopping would only ever save budget on a
    third-or-later attempt that doesn't exist by default — the whole point
    (stop wasting cost on an attempt already proven not to help) would be
    nearly vacuous at the default setting. A single unchanged retry is
    already conclusive: the fix attempt in question demonstrably altered
    nothing about the observed failure.
  - **A new terminal state (`no_progress`), not folded into
    `retry_exhausted`.** Distinct causes deserve a distinct label: exhausted
    retries with the failure *changing* each time means the agent tried
    different things and none worked yet (arguably still salvageable with a
    human's help pointing at the right angle); `no_progress` means a whole
    attempt provably produced zero effect, a stronger and more specific
    signal worth surfacing separately (see MANUAL.md's state table).
- **Entry-point checks: a static `ast` check plus a real smoke-run, not one
  or the other, and not an attempt to simulate interactive use.** User
  chose "both" after being shown the tradeoff directly. Considered three
  designs before landing here:
  - **Static-only.** Precisely targets the actual reported bug (order of
    definitions relative to the `__main__` guard) with zero execution risk
    — but only that bug class. A different startup crash (e.g. a bad
    top-level `import`, or an exception raised unconditionally inside
    `main()`) would sail through undetected.
  - **Smoke-run-only.** More general — genuinely executes the script — but
    cannot see the *reason* a crash happened, only that one did, and
    critically **cannot reach the actual reported bug at all**: with stdin
    closed, the process crashes with `EOFError` at the very first `input()`
    prompt, long before a user could ever type `SOLVE`. Confirmed by
    running it live against the real broken file. Smoke-run-only would have
    reported *a* failure, but for the wrong reason, and would not
    generalize to a version of the bug reachable later in a longer
    interaction.
  - **Attempting to drive the interactive session** (feed a plausible
    command sequence via stdin, e.g. guess `"3\nSOLVE\n"` for this
    project) — rejected outright, not just deprioritized: there is no
    general, safe way to guess what an arbitrary generated program expects
    to read, for what commands, in what order. A wrong guess either
    produces a misleading failure (blaming the program for not
    understanding an input this harness invented) or gives false
    confidence (a guess that happens not to exercise the buggy branch).
    Doing this reliably would require either the agent's own declared
    interaction script (not requested, real scope) or genuine automation
    of the interactive session, neither of which this pass attempted -
    documented plainly in MANUAL.md's "Known limitations" as a real,
    permanent gap rather than something papered over.
  Landed on **both static and smoke-run, each contributing independently**:
  together they cover "this bug shape, precisely" and "does it even start,
  generally" — two different, complementary slices of "does the entry point
  actually work," while being explicit in the docs about the interactive-path
  gap neither closes.
  - **`CheckSpec.precomputed` added as a minimal, general extension**
    rather than a special case bolted onto `execute_check` for this one
    check. A static check is categorically different from every other check
    in this module (no subprocess, result known at planning time) — giving
    it a real field in the existing planning/execution boundary (`execute_check`
    returns `spec.precomputed` unchanged when set) keeps stage 3
    ("command execution") honestly describable as "the only stage that
    spawns a subprocess — except when a spec says it doesn't need to,"
    rather than teaching `execute_check` a one-off bypass just for this
    check. Reusable by any future static check without another special case.
  - **`stdin=subprocess.DEVNULL` made the default for every check via
    `execute_check`, not a flag only the entry-point smoke-run opts into.**
    Every existing check (pytest, ruff, mypy, npm, go, cargo, mvn/gradle)
    already shouldn't need to read stdin in a properly automated
    invocation; before this change none of them explicitly closed it,
    meaning any of them could in principle inherit the harness's own real
    stdin and hang a check indefinitely instead of failing fast — a latent
    risk this change closes globally, not just for the one check that
    actually needed it. Consistent with the same "no human is available to
    answer a prompt" principle `agent.py`'s `_NONINTERACTIVE_TOOLING_SUFFIX`
    already applies to the agent's own tool calls.
  - **Entry points were originally looked for only at the Python project's
    root directory, not recursively — since revisited (see the "Recursive
    entry-point discovery" entry below).** Matches the kind of small,
    single-script generated project this harness actually verified at the
    time (confirmed by every live repro in this file so far); a deeper
    search would risk picking up an unrelated `__main__` guard in, say, a
    vendored dependency or an example script, and adds real cost (another
    tree walk) for a case not yet observed in practice. Documented as a
    real scope limit, revisitable if a nested-entry-point project is ever
    actually seen.
- **Recursive entry-point discovery: reused `detect_project()`'s own
  `_SKIP_DIRS`/`_MAX_SCAN_DEPTH` walk pattern, plus one new,
  purpose-specific skip set — not a second bespoke walk implementation.**
  Revisits the scope limit named directly above; the todo item itself
  named the exact risk a recursive walk would reintroduce (an unrelated
  `__main__` guard inside a vendored dependency), already solved once for
  `detect_project()`'s own tree walk via `_SKIP_DIRS`/`_MAX_SCAN_DEPTH` —
  reusing that established, already-tested pattern for
  `_find_python_entrypoints()` was lower-risk than inventing new
  depth-bounding/skip logic for a second walk.
  - **A separate `_ENTRYPOINT_SKIP_DIRS = _SKIP_DIRS | {"tests", "test"}`
    set, not a mutation of the shared `_SKIP_DIRS` global.** Recursion
    surfaces a second false-positive risk the todo item's text didn't name
    explicitly but is clearly within its spirit: a project's own
    `tests`/`test` directory can contain a script with its own
    `if __name__ == "__main__": unittest.main()`, which is not "the
    program's own entry point" once the walk goes recursive (it was never
    reachable before, since entry-point discovery only looked at the
    project root). `_SKIP_DIRS` is also used by `detect_project()`'s own,
    unrelated walk, where excluding `tests`/`test` would be semantically
    wrong (a project's tests are relevant to detecting it's a Python
    project) — so this needed a second, purpose-specific set rather than
    changing the shared one.
  - **Return values stay relative paths from `os.path.relpath(path, root)`,
    preserving exact backward compatibility for a root-level entry
    point** (a bare filename, e.g. `"hanoi.py"`, unchanged) while a nested
    one now reports as e.g. `"src/app/main.py"` — downstream consumers
    (`_entrypoint_ordering_spec`, the smoke-run `CheckSpec`'s
    `command=[*_python_command(), name]` with `cwd=root`) needed no
    changes at all, since `os.path.join(root, name)` and running
    `python <relative-path>` with `cwd=root` already handle a nested
    relative path correctly.
  - Verified live: a synthetic project with a nested entry point
    (`src/app/main.py`), a vendored dependency's own `__main__` guard
    (`vendor/somelib/cli.py`), and a test script with its own guard
    (`tests/test_cli.py`) correctly discovers only the real nested entry
    point — and the existing `projects/hanoi/` repro (a root-level entry
    point) still returns `["hanoi.py"]`, byte-for-byte as before this
    change.
- **`reasoning_effort` added as a fifth per-request LLM override field
  (`config.py`/`llm.py`/`cli.py`/`server.py`), reusing `override_llm()`
  rather than a parallel mechanism.** Prompted by "does our LLM abstraction
  support a reasoning-level param, and if so wire it everywhere." Verified
  the claim first rather than assuming it: read `openhands/sdk/llm/llm.py`
  directly and found `LLM.reasoning_effort: Literal["low", "medium", "high",
  "xhigh", "none"] | SkipJsonSchema[str] | None = "high"`, explicitly
  documented as provider-neutral and forward-compatible (LiteLLM translates
  it per-provider; the SDK's own docstring says it accepts values beyond the
  ones listed). The SDK also exposes `reasoning_summary` (OpenAI-specific,
  needs a verified org), `extended_thinking_budget` (legacy Anthropic-only
  token budget — its own docstring says "prefer reasoning_effort for new
  integrations"), and `enable_encrypted_reasoning` — none of these three were
  wired in; `reasoning_effort` is the one the SDK itself recommends and the
  only one that's provider-agnostic, matching this project's model-agnostic
  invariant. Deliberately **not** validated against a fixed choice list in
  `config.py` (unlike `CONFIRM_MODES`/`EXECUTION_MODES`/`VERIFY_TESTS_MODES`,
  which use `_parse_choice()`): the SDK's own field type is deliberately
  open-ended for forward compatibility, so a harness-side strict validator
  would reject a legitimate future value before the SDK/LiteLLM even got a
  chance to translate it. Two implementation subtleties worth remembering if
  this pattern is repeated for a future field: (1) the new `Config` field had
  to be added *after* every other field with a default, not merely after
  `base_url` — a dataclass field with a default can't precede one without,
  and `workspace` (no default) immediately followed `base_url`, so the naive
  placement raised `TypeError` at import time; (2) `llm.py`'s `build_llm()`
  must only pass `reasoning_effort=...` to `LLM(...)` when
  `cfg.reasoning_effort` is actually set — pydantic treats an explicit
  `reasoning_effort=None` as a real value distinct from "omitted," so passing
  it unconditionally would silently override the SDK's own `"high"` default
  with `None` whenever no override was configured; the fix builds a
  conditional kwargs dict instead of passing the field directly. Also unlike
  `model` (`override_llm(cfg, model="   ")` raises), a blank
  `reasoning_effort` override does not raise — it clears back to "unset" so
  the SDK's default applies, matching `api_key`/`base_url`'s leniency, since
  "go back to the SDK default" is a legitimate thing to ask for and there's
  no equivalent of `model`'s "can't run with nothing selected" failure mode.
- **Task-store persistence: a pluggable `TaskStore` ABC with five backends
  (memory/redis/sqlite/mysql/postgres), not just a TTL purge on the
  existing in-memory dict.** The backlog item as originally scoped was
  narrower ("a TTL-based purge is a small, self-contained improvement; a
  persistent store is a bigger step and only worth it if actually needed")
  — the user's actual ask expanded this explicitly: full `.env`
  configurability across all five backends, "keep forever" as a real
  option, and delete-by-project. Two real architectural forks were
  resolved via `AskUserQuestion` rather than picked unilaterally, since
  both affect long-term maintenance cost, not just this change's size:
  - **SQLAlchemy Core for all three SQL backends, not three hand-written
    implementations** (user chose "SQLAlchemy (recommended)"). One
    `SQLTaskStore`, parameterized only by connection URL, versus three
    independent modules each hand-rolling its own DBAPI calls — SQLAlchemy
    Core (not the ORM: there's exactly one table, no relationships,
    `TaskRecord` is already a plain dataclass) gets connection pooling and
    parameter binding for free and means one query-logic bug fix instead
    of three. Upsert is implemented as "try UPDATE, INSERT if 0 rows
    affected" rather than a dialect-specific `ON CONFLICT`/`ON DUPLICATE
    KEY UPDATE` — slightly less efficient on first insert (two round
    trips) but the same code path works unmodified on sqlite/mysql/
    postgres, which is the entire point of sharing an implementation.
  - **Delete-by-project exposed via both REST and CLI** (user chose
    "Both" over either alone). Different callers need different access:
    an application already talking to the running server over HTTP
    shouldn't need a separate CLI invocation and its own copy of
    `.env`/credentials; a one-off maintenance script, a cron job, or
    cleanup against a backend that isn't the one a particular server
    process is currently configured for benefits from a direct CLI that
    doesn't require a server to be up at all. Both end up calling the same
    `TaskStore.delete_by_project()` — no duplicated deletion logic, just
    two thin entry points.
  - **Redis TTL: native per-key expiration (`EX` at write time), not a
    purge sweep.** Every other backend's `purge_expired()` does real work
    (a `DELETE ... WHERE updated_at < cutoff`-shaped query); Redis's is a
    deliberate no-op. Redis already has the exact mechanism this problem
    needs built in — writing a second, harness-side sweep on top would be
    slower (a polling loop instead of instant, server-side expiry),
    redundant, and a second place the same bug (e.g. a wrong cutoff
    calculation) could be introduced. `ttl_seconds` is fixed at
    `RedisTaskStore.__init__` (from `cfg.task_ttl_seconds`), not a
    per-`save()` parameter, specifically so `TaskStore.save(record)`'s
    signature stays identical across every backend — `server.py` must
    never need a backend-specific branch to call it.
  - **`MemoryTaskStore.save()`/`get()` deep-copy the record — a
    self-caught correctness fix, not a request.** The original in-memory
    dict returned the *same* object reference a caller had handed it,
    which was fine under the old code's single coarse `tasks_lock`
    wrapping every mutation *and* every read together. Once that lock was
    replaced by each backend's own internal locking (needed regardless,
    since `server.py` no longer knows or cares which backend is active),
    a live reference would let a concurrent `GET /tasks/{id}` observe a
    record mid-mutation (the background task thread sets several fields
    across several lines before its next `save()`), or let a caller
    mutate a `get()`-returned record and have it "stick" with no explicit
    `save()` — a bug that would only ever surface after switching to a
    real backend, since SQL/Redis can only ever hand back fresh,
    independently-deserialized snapshots, never a live handle. Standardizing
    `MemoryTaskStore` on the same snapshot semantics via `copy.deepcopy`
    in both methods makes all five backends behave identically, which is
    the correct fix, not just a defensive one.
  - **`create_app()` now builds the store once at startup
    (`build_task_store(startup_cfg)`), which requires a valid `LLM_MODEL`/
    `LLM_API_KEY` at server-start time rather than only on the first
    request — a deliberate, minor behavior change.** A store may hold real
    resources (a DB connection pool, a Redis client) that can't sensibly
    be rebuilt per-request the way a per-request LLM override already is;
    building it once at startup is the only sensible lifecycle for those.
    Fail-fast (a broken `.env` refuses to start the server at all) beats
    silently starting a server that will 500 on its first real request —
    consistent with `_resolve_cfg`'s per-request `ConfigError`s already
    being surfaced as clean `400`s rather than crashing the process.
  - **`sqlite` needs only the `sqlalchemy` extra, not a DBAPI driver
    package** — sqlite's driver (`sqlite3`) is Python's own standard
    library; `mysql`/`postgres` each need a real separate extra
    (`pymysql`, `psycopg2-binary`) on top of `sqlalchemy` since a given
    deployment typically only uses one SQL backend and shouldn't be forced
    to install drivers for the other two.
  - **`mysql`/`postgres` were verified live against real Docker containers
    (`postgres:16-alpine`, `mysql:8`) during development but are
    deliberately not part of the automated `pytest` suite** — unlike
    `sqlite` (a plain file, `tmp_path`-backed, zero external
    dependencies) and `redis` (gated by
    `pytest.mark.skipif(shutil.which("redis-server") is None, ...)`,
    matching this repo's existing convention of spawning a real local
    binary rather than mocking it), requiring Docker in every test
    environment (including CI, if this project ever adds it) is a bigger
    ask than this feature justifies. `test_task_store.py`'s module
    docstring states this explicitly rather than leaving "why isn't mysql/
    postgres tested" to be rediscovered later.
- **Monorepo/nested-project ambiguity: a new `"ambiguous"` `ProjectDetection`
  state carrying every candidate, not silently picking the directory-walk
  order's first match.** `detect_project()`'s existing docstring already
  claimed "shallowest directory wins," but that wasn't actually true for
  matches in different branches of the tree: `os.walk` is a DFS, so it
  fully explores a first subdirectory (however deep) before ever visiting
  a shallower sibling — a manifest two levels down a first-visited branch
  would be returned even if an unrelated project's manifest sat one level
  down a not-yet-visited sibling. This was a real, if subtle, correctness
  gap for a monorepo/workspace shape (e.g. sibling
  `frontend/package.json` and `backend/pyproject.toml`), not just a
  theoretical one — before this fix, which of the two got "verified" (and
  which got silently ignored) depended on filesystem/`os.scandir` listing
  order, not anything meaningful about the project.
  - **Fixed by collecting every match grouped by depth during the same
    single tree walk, then comparing only the truly shallowest depth's
    matches**, rather than returning on the first hit. This both fixes the
    "shallowest actually wins" claim (making the docstring accurate) and
    is the mechanism ambiguity detection needed anyway (comparing what's
    at the winning depth). A depth with exactly one match behaves exactly
    as before; a depth with more than one distinct directory becomes
    `"ambiguous"`.
  - **The workspace root itself (depth 0) is checked and returned
    immediately, before any walk-driven comparison, and is never subject
    to "ambiguous."** The root is what the caller (`HARNESS_WORKSPACE`/
    `--project`) already told the harness the project *is* — a deeper
    manifest elsewhere under it (a vendored dependency, a nested example
    app) is not a second candidate for "what is the project," it's just
    something else that happens to exist underneath it. This is also
    exactly the "prefer an explicit project root when one is supplied"
    half of the original ask — the existing per-request/`--project`
    mechanism already *is* that explicit root; no new parameter was needed
    to satisfy it, only making sure it's never overridden by ambiguity
    logic.
  - **Same-directory multi-marker ties (e.g. both `pyproject.toml` and
    `package.json` in one directory) are deliberately not treated as
    ambiguous** — resolved by the pre-existing `_LANGUAGE_MARKERS` priority
    order (Python wins), same as before this change. That case was already
    explicitly called out in the marker table's own comment as "an
    unscoped monorepo edge case, not a real per-project decision" — one
    directory with two manifest files is a different, narrower question
    than "which of several separate directories is the project," and
    conflating the two would have turned an already-accepted, documented
    simplification into a behavior change with no clear improvement (there
    is no more "correct" choice between two markers in the same directory
    than the existing priority order already makes).
  - **Ambiguity resolves to a single synthetic `unavailable` `CheckSpec`
    naming every candidate's language and root**, reusing exactly the same
    shape `discover_verification_plan()` already used for `"unknown"` —
    `VerificationRun.state` becomes `"inconclusive"` (not a new state on
    that enum) and the candidate list surfaces via the existing
    `limitation_notes` mechanism. Chosen over inventing a distinct
    aggregate outcome (e.g. `"ambiguous"` as a `VerificationRun.state`
    value) because "we don't know what to verify" is exactly what
    `"unknown"`/`inconclusive` already means — the *reason* differs
    (multiple candidates vs. none recognized) but the caller-facing
    consequence (nothing was confirmed working, here's why) is identical,
    and reusing the existing plumbing meant no changes to `runner.py`,
    `CompletionContract`, or any caller's state-handling code at all — only
    `run_tests_tool.py` needed to change.
  - **The agent-facing `run_tests` tool required zero code changes** — it
    already routed every non-Python/Node language (including `"unknown"`)
    through the same generic `discover_verification_plan()`/`execute_check()`
    path instead of a hardcoded branch per language, so `"ambiguous"`
    picked up the same handling automatically; only a comment documenting
    the new possible `check_kind` value was added.
  - Verified live against a real on-disk monorepo fixture (sibling
    `frontend/package.json` + `backend/pyproject.toml`, neither at the
    workspace root): `run_full_verification()` reports `state ==
    "inconclusive"` with a single `unavailable` check naming both
    candidate paths and languages by their real absolute paths — not
    simulated, an actual two-project directory tree on disk.
- **Opt-in interactive mode (`HARNESS_INTERACTIVE`/`--interactive`): the
  checkpoint wraps only the *initial* run, not the automated
  task_tracker/verify retry loops — confirmed with the user via
  `AskUserQuestion` before implementing, per the todo item's own explicit
  request for "real design work... before implementation, not a quick
  patch."** Two shapes were on the table: (a) a checkpoint only around the
  initial `conversation.run()`, or (b) a checkpoint at every point any of
  the three call sites (initial run, task_tracker retries, verify retries)
  reaches `FINISHED`. Chose (a) — the smallest change that covers the two
  concrete failure modes already on record (a clarifying question, a
  premature "done" claim, both from the *initial* run in every real
  repro logged in this file), without threading a new callback through
  `_enforce_task_tracker_completion`/`_verify_and_report` or deciding how
  a human's reply should interleave with the harness's own automated
  "your tests are still failing, fix them" follow-up inside those loops —
  a real design question (b) would raise that (a) sidesteps entirely by
  leaving those two loops completely untouched.
  - **Callback shape: `OnAwaitingInput = Callable[[str], str | None]`** —
    takes the narrative text since the last checkpoint, returns the human's
    reply or a falsy value to end the loop. Mirrors `ConfirmCallback`/
    `_confirm_pending_actions`'s existing self-contained "receives what it
    needs, does its own printing and prompting" shape rather than a
    zero-arg callback that would have required `cli.py` to also wire live
    message-streaming just for this (`run_task`'s collect-and-return shape
    stays untouched).
  - **`_narrative_text()` is a separate, small copy of `server.py`'s
    `_narrative_texts`, not a shared import** — `server.py` depends on
    `runner.py`, not the other way around, so importing it the other
    direction would be a real layering violation, not just a style
    preference; ~15 duplicated lines is the correct trade here (same "three
    similar lines over a premature/backwards abstraction" call made
    elsewhere in this file for `acceptance.py`'s path-containment logic).
  - **`HARNESS_MAX_TASK_SECONDS` is paused while blocked on the human's
    reply, not spent by it** — `deadline` is pushed forward by the exact
    wall-clock time spent inside `on_awaiting_input()` before the next
    `_run_with_confirmation` call. Without this, a real back-and-forth
    conversation could exhaust the shared task budget purely from a human
    taking their time to type, which has nothing to do with what the
    budget exists to bound (runaway *agent* execution — see the matching
    decisions-log entry above). Verified with a fake clock that only
    advances when explicitly told to (not a fixed-value iterator sequence,
    which would be brittle to the exact number of `time.monotonic()` calls
    a refactor might add or remove): a simulated "wait" of 999 seconds
    against a 5-second `HARNESS_MAX_TASK_SECONDS` still completes normally
    instead of reporting `budget_exhausted`.
  - **Checkpoint fires only when `execution_status == FINISHED`** — not
    `STUCK`/`ERROR`, which already produce their own outcome via the
    existing aborted-status handling; interactive mode doesn't change how
    those are handled at all.
  - **No new system-prompt suffix was added — only `_AUTONOMOUS_SUFFIX` is
    dropped when `cfg.interactive` is true, exactly matching the todo
    item's own proposed shape** (drop (a), wire the terminal loop (b)).
    Every other suffix (`_README_SUFFIX`, `_NONINTERACTIVE_TOOLING_SUFFIX`,
    `_VERIFY_BEFORE_FINISH_SUFFIX`, `_LIFECYCLE_SKILLS_SUFFIX`) stays
    unconditional — none of them are about whether a human is present,
    they're independent policies.
  - **`HARNESS_INTERACTIVE` has no effect without a caller-supplied
    `on_awaiting_input`** — only `cli.py` wires one; `server.py` doesn't
    (no terminal to prompt at), the same gap `HARNESS_CONFIRM_MODE=always`
    already has for server mode. Setting the flag anyway still drops the
    autonomous nudge with nobody available to answer if the agent does ask
    something — documented plainly in MANUAL.md as a foot-gun rather than
    silently guarded against, matching the existing confirm-mode precedent.
  - Verified live, twice, against a real LLM call (not just unit tests):
    (1) a single-round task where pressing Enter immediately after the
    first `finish` proceeded straight to the existing autonomous
    verification flow, unchanged; (2) a multi-round conversation where a
    typed follow-up ("also create a second file...") was sent back to the
    same running conversation, which then created the second file and
    finished again, prompting a second time — confirming the interactive
    loop preserves full conversation context across replies rather than
    starting fresh. Also verified the default (`--interactive` omitted)
    behaves byte-for-byte as before: no prompt, same autonomous flow.
- **Deterministic auto model selection (`HARNESS_MODEL_SELECTION=auto`/
  `--auto-model`): designed collaboratively over an extended conversation
  before any code was written, per the user's own explicit "let's design
  the solution first."** The user proposed a hand-curated catalog of
  models rated on axes (reasoning, cost, precision, code, ...) with the
  harness picking the best fit per task. Several real architectural forks
  were resolved through discussion rather than picked unilaterally:
  - **Deterministic weighted scoring, not an LLM-based router** (user's
    explicit choice, after being asked directly) — no extra model call to
    decide, consistent with this project's existing precedent against
    adding an LLM call where a deterministic signal already suffices (see
    the "Skills: two separate mechanisms" entry above, which rejected an
    LLM classifier for the identical reason).
  - **One static ranked list per task, computed once, walked forward under
    either failure type — never re-classified or re-scored mid-task**
    (the user confirmed this as "my suggestion" after a full explanation
    of the tradeoff). The caveat this creates — falling back moves to the
    next-best-*fit*, not necessarily a strictly more capable model — is
    documented plainly in MANUAL.md rather than solved, since solving it
    (re-weighting toward raw capability on a quality-triggered escalation
    specifically) was explicitly deferred as a real v1.1 idea, not
    silently absorbed into v1's scope.
  - **No hard minimum catalog size** (the user reversed an initial
    proposal to require ≥3 entries) — `rank_candidates()` just produces a
    shorter chain with fewer models; a 1-entry catalog behaves like no
    fallback chain at all rather than a config error.
  - **Real API keys live directly in `models.yaml`, not indirected through
    named env-var references** (the user's explicit choice over an
    `api_key_env: SOME_VAR` alternative that was proposed) — so it needs
    exactly `.env`'s own treatment: added to `.gitignore` next to `.env`/
    `.env.local`/`secrets/`, and `models.yaml.example` (blank secrets) is
    the committed template, matching the `.env`/`.env.example` split
    exactly.
  - **Task-profile weight vectors live in the same `models.yaml` file**
    (not a separate config file, and not hardcoded harness policy) — the
    user's answer covered both "models are configurable" and, by not
    distinguishing them, implied the weighting logic should be too; kept
    in one file since both describe "how a model gets chosen," the same
    reasoning already applied to keeping ratings and descriptions
    together on one entry.
  - **Visibility: `MODEL_DECISIONS.md` in the project workspace** (the
    user's explicit choice of format and placement — "markdown, give it a
    standard name with .md extension" — over a JSON alternative that was
    offered) plus the same records on `TaskOutcome.model_decisions` for
    programmatic callers, matching the existing `completion_contract`/
    `acceptance_results` precedent of always surfacing structured outcome
    data both ways.
  - **`server.py` needed zero code changes.** `HARNESS_MODEL_SELECTION`/
    `HARNESS_MODELS_FILE` already flow through `_resolve_cfg`'s existing
    `load_config()` → (optional per-request `override_llm`/`replace`) →
    `stream_task(cfg=cfg, ...)` pipeline untouched, since none of those
    steps ever drop or reset fields they don't explicitly touch. `on_model_
    choice` is simply never passed, the same "no terminal to prompt at"
    pattern already established for `on_confirm`/`on_awaiting_input` —
    confirmed by tracing the actual call sites before writing any server.py
    changes, not assumed, and none were needed. Surfacing
    `model_decisions` through the persistent `TaskRecord`/SQL schema (for
    `GET /tasks/{id}`) was deliberately **not** done in this pass — it
    would need a real schema migration story for already-deployed
    sqlite/mysql/postgres task stores that this change never asked for;
    left as a genuine follow-up rather than an unplanned, unreviewed
    schema change bundled into an unrelated feature.
  - **Docker gets the initial pick but not mid-task fallback** — verified
    directly against the SDK source (not assumed) that
    `conversation.switch_llm`/`switch_profile`/`get_or_create_profile_llm`
    exist on `LocalConversation` but not `RemoteConversation`'s Python
    client, even though the remote agent-server's own REST API already
    exposes a matching `switch_llm` endpoint server-side (its docstring
    literally describes this harness's exact use case — an app-server that
    owns the LLM directly). Building against `RemoteConversation`'s
    private `_client`/`_id` attributes to call that endpoint directly was
    considered and rejected — depending on undocumented, unstable internals
    is exactly the SDK-drift risk this project's golden rule 2 exists to
    avoid, for a gap that's realistically closed by an upstream SDK
    release rather than a workaround. Documented as a plain, known
    limitation (MANUAL.md "Docker") rather than worked around.
  - **Finding the actual exception type to catch took two full rounds of
    live verification, not one** — a genuinely instructive case of "verify,
    don't assume" holding up under repeated testing, not just a single
    check. First guess: `litellm.exceptions.APIError`. Wrong — inspecting
    each concrete exception class's `__mro__` by *name only*
    (`RateLimitError`, `APIStatusError`, `APIError`, ...) looked like a
    match, but the fully-qualified classes revealed every one of them
    actually subclasses the *same-named* class from the `openai` package,
    not `litellm.exceptions`' own base. Second guess, corrected for that:
    `openai.OpenAIError` (confirmed against all 20 of LiteLLM's own
    declared `LITELLM_EXCEPTION_TYPES`, all of which really do subclass
    it). Still wrong in production — caught only by an actual live
    `conversation.run()` call against a deliberately invalid API key: the
    SDK wraps every LLM-call failure in its *own*
    `openhands.sdk.llm.exceptions.types.LLMError` hierarchy
    (`LLMAuthenticationError`/`LLMRateLimitError`/`LLMTimeoutError`/
    `LLMContextWindowExceedError`/`LLMServiceUnavailableError`/...) before
    `ConversationRunError.original_exception` is ever set —
    `openai.OpenAIError` and `litellm.exceptions.APIError` are both
    invisible to that wrapping entirely. `LLMError` (the SDK's own,
    already-a-direct-dependency type) is what `_run_conversation_once`
    actually checks against — no extra `openai`/`litellm` dependency ended
    up being needed in `pyproject.toml` at all, only `pyyaml` (for
    `model_catalog.py`'s own YAML parsing).
  - **`write_model_decisions()` runs in a `finally` block around the whole
    task, not just after a normal return — a bug caught live, not by
    inspection.** The first implementation wrote `MODEL_DECISIONS.md` only
    at the end of `stream_task`'s normal control flow; a live run with a
    deliberately-exhausted fallback chain (three candidates, all given
    invalid keys) raised all the way out of `stream_task` before that line
    ever executed, silently losing the *entire* escalation history for
    exactly the case where a human debugging the failure needs it most.
    Fixed by extracting the initial-run/interactive/retry phases into a
    separate `_run_stream_task_phases()` helper and wrapping its call in
    `try/finally`, writing whatever decisions had accumulated regardless
    of whether the call returned or raised. Re-verified live against the
    same three-candidate-all-invalid scenario after the fix:
    `MODEL_DECISIONS.md` correctly shows all three attempts (`initial` +
    two `escalation_api_failure` entries) before the final, real
    `AuthenticationError` propagates to the caller.
  - Verified live end-to-end against real API calls (not mocked
    exceptions) with deliberately invalid keys across three real providers
    (Anthropic ×2, OpenAI): the full cascade — `strong` (Anthropic) fails
    → escalates to `balanced` (Anthropic) → fails → escalates to `cheap`
    (OpenAI) → fails → chain exhausted, real `AuthenticationError`
    re-raised — matched exactly what `MODEL_DECISIONS.md` and the terminal
    output recorded, model-by-model, reason-by-reason.
- **Per-run artifacts directory (`HARNESS_ARTIFACTS_DIR`): a separate,
  sibling top-level directory, not a folder nested inside each project —
  designed collaboratively, same process as auto model selection.** User
  asked for a per-project place to inspect docs/analytics/metadata about
  how a build went, floating "external... must be in `.env`" as the shape
  — confirmed directly (not assumed) this reads as "mirror
  `HARNESS_PROJECTS_DIR`'s own shape," not a hidden folder inside each
  generated project, since the latter would mean writing a `.gitignore`
  entry into a project the harness doesn't own.
  - **`conversation.conversation_stats` discovery — the key fact that made
    this cheap to build.** Read `openhands.sdk.llm.utils.metrics.Metrics`
    and `ConversationStats` source directly before proposing anything:
    `get_combined_metrics().get()` returns a real dict (`accumulated_cost`,
    full token breakdown, per-call cost/latency/token-usage lists), and
    `usage_to_metrics` is `dict[usage_id, Metrics]` — since auto model
    selection already gives each catalog candidate its own `usage_id`, a
    **per-model cost/token breakdown falls out for free**, zero new
    plumbing beyond calling `.get()` on each entry. Also confirmed
    `conversation_stats` exists on both `LocalConversation` and
    `RemoteConversation` — unlike model selection's `switch_llm`, this
    feature has no docker limitation at all.
  - **`MODEL_DECISIONS.md` explicitly does not move** (user's explicit
    choice) — this directory is for genuinely new data (transcripts,
    tokens, cost), not a relocation of something that already works.
  - **A run ID is minted, not left to the caller** (user's explicit
    choice — "it's better having an automatically generated id"). `cli.py`
    has no natural task identity today, so `stream_task` mints one
    (`uuid.uuid4()`) whenever `cfg.artifacts_dir` is set and none was
    given. `server.py` passes its own `TaskRecord.id` instead, so a task's
    artifacts folder always matches what `GET /tasks/{id}` returns — the
    one case where reusing an existing ID beats minting a fresh,
    disconnected one.
  - **A new `project: str | None` parameter on `stream_task`/`run_task`
    was required, not just plumbing that already existed.** `Config` was
    found, on inspection, to have no field for the project *name* at
    all — only `workspace`, the already-resolved path `resolve_project_dir`
    produces. `cli.py`/`server.py` both already hold the name before
    resolving it, so they pass it again here, purely to bucket the
    artifacts folder the same way `HARNESS_PROJECTS_DIR` is bucketed.
  - **Path containment is a separate implementation from
    `config.resolve_project_dir()`, not a shared call** — same exact
    reasoning as `acceptance.py`'s own containment check and model
    selection's `config_for_entry` (see their entries above): the
    existing function's error text hardcodes "HARNESS_PROJECTS_DIR",
    which would be actively wrong for a different root.
  - **A live-caught bug, not just a plan followed as written:** the first
    implementation only mounted the `write_run_artifacts()` call inside
    the same success path as `write_model_decisions()`, not inside a
    `finally` guarding the whole task — meaning a task whose model-
    selection chain was fully exhausted (an already-tested failure mode)
    would raise *before* any artifacts were written at all, silently
    losing the transcript and metrics for exactly the run most worth
    debugging. Fixed by capturing the exception via `except BaseException
    as exc: captured_error = exc; raise` and moving the
    `write_run_artifacts()` call into the existing `finally` block
    alongside `write_model_decisions()` — the second time in this file a
    "write artifacts even on failure" bug was caught only by testing the
    already-known-risky path (an exhausted fallback chain), not by
    inspection.
  - Verified live end-to-end against a real LLM call: real token counts
    (19,053 prompt / 272 completion tokens), a real cost
    (`$0.00293586`), and a real 6-message transcript, all correctly
    written to `metadata.json`/`metrics.json`/`transcript.json`; a
    `--project` run correctly bucketed under
    `<artifacts_dir>/<project>/<run_id>/`; and the default
    (`HARNESS_ARTIFACTS_DIR` unset) confirmed to write nothing at all —
    no `artifacts` directory created anywhere.
- **Removed `_find_marker_dir()` from `run_tests_tool.py` — genuine dead
  code, not a behavior change.** Defined but never called anywhere in
  `src/`, confirmed both when first noticed (during item #21's
  `detect_project()` rework, which didn't need it either) and again
  immediately before deleting it. Full suite (536 tests) unaffected.
- **`models.yaml` gained a per-model `activated` (bool, default `true`)
  field — filtered in `rank_candidates()`, not at `load_model_catalog()`
  parse time.** User request, to maintain a large candidate catalog and
  toggle individual models on/off (e.g. a provider they don't currently
  have a key for) without deleting/re-adding entries. Filtering happens in
  `rank_candidates()` rather than dropping deactivated entries from
  `ModelCatalog.models` at load time: a deactivated entry still needs
  full schema validation and still needs to participate in
  `load_model_catalog()`'s duplicate-name check (an inactive entry sharing
  a name with an active one is still a real config mistake worth catching
  immediately, not just once it's re-activated) — dropping it earlier
  would have hidden both. Raises `ModelCatalogError` if every model ends
  up deactivated (rather than propagating `ModelChain`'s generic "requires
  at least one candidate" `ValueError`), since that's a specific,
  actionable, easily-made mistake (toggling off the last active model)
  that deserves a message naming the actual cause. Verified live against
  the real `models.yaml.example` (14 models, all `activated: true`
  parsing correctly) plus targeted unit tests for the omitted/explicit-
  true/explicit-false/non-boolean parse cases, the duplicate-name check
  still firing when one of the two duplicates is deactivated, and
  `rank_candidates()` both excluding a higher-scoring deactivated entry
  and raising when every entry is deactivated.
