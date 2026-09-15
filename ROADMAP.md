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
  `override_llm(cfg, model=, api_key=, base_url=)` returns a copy of `Config`
  with only the given fields replaced — the per-request/per-run override
  mechanism shared by `cli.py` and `server.py` (see MANUAL.md "Switching LLM
  provider / model" → "Per-request override").
- `llm.py` / `tools.py` / `agent.py` — SDK wiring; default preset tools + `run_tests`.
  `agent.py`'s `AgentContext.system_message_suffix` also carries four
  behavioral policies, not just SDK plumbing: `_AUTONOMOUS_SUFFIX` (see
  "Known limitations" below), `_README_SUFFIX`, which tells every agent to
  leave a `README.md` with concrete run instructions in the project root
  before finishing even when the task didn't ask for one (see MANUAL.md
  "Projects" → "README.md"), `_NONINTERACTIVE_TOOLING_SUFFIX`, which tells
  the agent to use CLI tools' non-interactive/CI flags and not blindly retry
  a command that produced no visible result, and `_VERIFY_BEFORE_FINISH_SUFFIX`,
  which tells the agent to actually run its tests/program before calling
  `finish` rather than describing untested claims as fact (see "Known
  limitations" below for both).
- `runner.py` — `stream_task()` (callback-per-message) is the shared primitive;
  `run_task()` wraps it for the CLI's collect-and-return use case. Also now
  wires `cfg.max_iterations` into `Conversation(max_iteration_per_run=...)`
  (previously unset, silently defaulting to the SDK's 500 — see "Known
  limitations" below), and runs `_verify_tests_and_retry()` after every
  `conversation.run()`: a harness-side, no-LLM re-run of the project's own
  tests via `run_tests_tool`'s executor directly, resending real failures and
  re-running the agent (bounded by `HARNESS_MAX_VERIFY_RETRIES`) instead of
  trusting the agent's self-report — see MANUAL.md "Test verification".
- `custom_tools/run_tests_tool.py` — verifies the project actually works;
  auto-detects pytest (Python markers, or none found — original default) vs.
  a Node project with a `package.json` `build` script (`npm run build`),
  structured results either way; registers at import time (not just on
  demand) so both `local` execution and the Docker image's
  `--import-modules` mechanism pick it up. See "Decisions log" below for why
  this generalization was added.
- `cli.py` — `python -m harness "<task>" [--execution] [--project] [--agents-md]
  [--model] [--api-key] [--base-url]`. `task` is resolved via
  `resolve_task_source()`: http(s) URL (fetched) or an existing local file
  (read) take precedence over literal text. CLI-only — server mode's `task`
  field does not do this resolution.
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
  `WS /tasks/stream` accept optional `model`/`api_key`/`base_url` overrides
  (via `config.override_llm`); `/v1/chat/completions` accepts the same thing
  under `llm_model`/`llm_api_key`/`llm_base_url` — kept distinct from its
  wire-mandated `model` field, which stays echo-only (see decisions log).
- `skills.py` — two mechanisms, don't conflate them: `load_skill_catalog()`
  loads the shared, reusable, trigger-based catalog (`skills/`, arbitrary
  subfolders for classification) into every agent's `AgentContext`
  (`agent.py`); `write_project_context()` writes a caller-supplied,
  project-specific `AGENTS.md` (CLI `--agents-md`, REST `agents_md`, both
  requiring `--project`/`project`). See MANUAL.md "Skills" for the full
  writeup and CLAUDE.md-linked rationale.

## Backlog — optional / not yet built

- **ECS/EC2 execution backends** — `workspace.py`'s `build_workspace()` is the
  single dispatch point; adding one is a new branch there plus a new
  `HARNESS_EXECUTION` value, not a rewrite. Nothing beyond `docker` exists.
- **`HARNESS_CONFIRM_MODE=always` wiring** — parsed/validated but not
  connected to an actual pause-before-tool-call gate. Needs `/verify-sdk` on
  the SDK's confirmation-policy API first (explicitly called out as
  unverified in `CLAUDE.md`).
- **`run_tests` generalization — partially done.** Now detects pytest vs. a
  Node project with a `package.json` `build` script and runs `npm run build`
  for the latter (see "Decisions log" and "Known limitations" below); this
  covers the concrete failure that motivated it (a JSX parse error that
  pytest-only verification couldn't see). **Still not done:** a JS project's
  actual test runner (jest/vitest/etc.) is not detected or run — only its
  build/compile step — and no ecosystem beyond Python/Node is covered (Go,
  Rust, Ruby, ...). Detecting an arbitrary project's real test runner and
  installing its deps remains a real scope question, not a quick fix.
- **Server-mode task registry persistence/cleanup** — in-memory, per-process,
  unbounded. Needs at least a TTL-based purge; a real store (Redis/DB) for
  persistence across restarts or multi-worker sharing is a bigger step.
- **Pin exact `openhands-sdk`/`openhands-tools` versions** in `pyproject.toml`
  — currently unpinned floating deps; noted as an open item since Milestone 1.
- **Milestone 3 live provider-swap proof** — blocked on a second provider key
  or local model endpoint (see table above).
- **Harness-side `task_tracker`-completion enforcement** — deferred in favor
  of trying the `_AUTONOMOUS_SUFFIX` prompt fix first (see "Known
  limitations" below and the matching decisions-log entry for the
  considered-and-rejected shape: `runner.py` inspecting the tracker after
  `conversation.run()` and auto-resending "continue — N items remain" in a
  loop). Only worth building if the prompt-only fix proves insufficient on
  re-test; needs its own stop-condition/cost-control design (a runaway
  "continue" loop is a real risk) before it's more than a sketch.
- **Surface `conversation.state.execution_status == STUCK` to the caller.**
  Noticed while investigating `Conversation`'s constructor for the
  `max_iteration_per_run` fix above: `stuck_detection=True` is already the
  SDK's own default (repeated action/observation loops, monologues,
  alternating patterns — thresholds in `StuckDetectionThresholds`), and a
  detected stuck loop silently ends the run the same way `FINISHED` does —
  today `runner.py` never even reads `execution_status`, so neither is
  distinguishable from a normal successful finish in the harness's own
  output. This is the same class of problem as the four `_AUTONOMOUS_SUFFIX`
  -family symptoms (a run ends without the caller knowing it didn't really
  complete), but at the SDK level rather than a prompt-following one — worth
  a small, cheap addition (check the status after each `conversation.run()`,
  emit a harness-authored notice on `STUCK`, same pattern as
  `_verify_tests_and_retry`'s give-up notice) the next time this file is
  touched for the same theme, not bundled into this change since it wasn't
  what was asked for.

## Known limitations (internal/architectural — see MANUAL.md for user-facing ones)

- **`HARNESS_MAX_ITERATIONS` was parsed/validated but never actually wired to
  anything — silently a no-op since Milestone 1, contrary to spec section 12's
  acceptance criterion ("`HARNESS_MAX_ITERATIONS` reliably bounds runaway
  loops").** Found while adding `runner.py`'s test-verification retry loop:
  `Conversation(...)` accepts `max_iteration_per_run` (SDK default `500`),
  which `runner.py` never passed, so every run silently used `500`
  regardless of `.env`. Fixed in the same change by passing
  `max_iteration_per_run=cfg.max_iterations`. No test previously caught this
  because no test asserted on iteration-capping behavior at all — worth a
  dedicated live-verified test if this class of gap (a config value parsed
  and validated but never actually consumed) recurs.
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
  in favor of trying the prompt fix first).
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

## Decisions log (why, not just what)

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
