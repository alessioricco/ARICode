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
| 6 | Optional (section 9) | Docker execution: done. Server mode: done (REST async submit+poll, WS streaming). Skills (formerly "microagents"): done — shared catalog + per-project AGENTS.md. OpenAI-compatible endpoint: not started. |

## What's implemented

- `config.py` — env parsing, model-agnostic (`LLM_MODEL` prefix selects provider).
- `llm.py` / `tools.py` / `agent.py` — SDK wiring; default preset tools + `run_tests`.
- `runner.py` — `stream_task()` (callback-per-message) is the shared primitive;
  `run_task()` wraps it for the CLI's collect-and-return use case.
- `custom_tools/run_tests_tool.py` — runs pytest, structured results; registers
  at import time (not just on demand) so both `local` execution and the
  Docker image's `--import-modules` mechanism pick it up.
- `cli.py` — `python -m harness "<task>" [--execution] [--project] [--agents-md]`.
  `task` is resolved via `resolve_task_source()`: http(s) URL (fetched) or an
  existing local file (read) take precedence over literal text. CLI-only —
  server mode's `task` field does not do this resolution.
- `workspace.py` — single dispatch point for execution backends
  (`build_workspace(cfg)`); `local` returns a plain path, `docker` returns a
  `DockerWorkspace`, both as context managers so cleanup is automatic.
- `docker/agent-server.Dockerfile` — layers our tool source onto
  `ghcr.io/openhands/agent-server`, built automatically on first use.
- `server.py` — `GET /health`, `POST /tasks` (async, returns immediately),
  `GET /tasks/{id}` (poll status/partial progress/result), `WS /tasks/stream`
  (live streaming). All three task-facing endpoints share one background
  thread + callback pattern.
- `skills.py` — two mechanisms, don't conflate them: `load_skill_catalog()`
  loads the shared, reusable, trigger-based catalog (`skills/`, arbitrary
  subfolders for classification) into every agent's `AgentContext`
  (`agent.py`); `write_project_context()` writes a caller-supplied,
  project-specific `AGENTS.md` (CLI `--agents-md`, REST `agents_md`, both
  requiring `--project`/`project`). See MANUAL.md "Skills" for the full
  writeup and CLAUDE.md-linked rationale.

## Backlog — optional / not yet built

- **OpenAI-compatible endpoint** — a `/v1/chat/completions`-shaped adapter for
  tools that only know how to talk to "OpenAI," distinct from the REST/WS
  server. Would need a translation layer mapping chat-completions
  requests/streaming to `run_task`/`stream_task` and back.
- **ECS/EC2 execution backends** — `workspace.py`'s `build_workspace()` is the
  single dispatch point; adding one is a new branch there plus a new
  `HARNESS_EXECUTION` value, not a rewrite. Nothing beyond `docker` exists.
- **`HARNESS_CONFIRM_MODE=always` wiring** — parsed/validated but not
  connected to an actual pause-before-tool-call gate. Needs `/verify-sdk` on
  the SDK's confirmation-policy API first (explicitly called out as
  unverified in `CLAUDE.md`).
- **`run_tests` generalization** — currently assumes a pytest-based project
  with pytest installed (true for this repo's own suite, not for arbitrary
  `--project` workspaces or the Docker image). Detecting the actual test
  runner and its deps is a real scope question, not a quick fix.
- **Server-mode task registry persistence/cleanup** — in-memory, per-process,
  unbounded. Needs at least a TTL-based purge; a real store (Redis/DB) for
  persistence across restarts or multi-worker sharing is a bigger step.
- **Pin exact `openhands-sdk`/`openhands-tools` versions** in `pyproject.toml`
  — currently unpinned floating deps; noted as an open item since Milestone 1.
- **Milestone 3 live provider-swap proof** — blocked on a second provider key
  or local model endpoint (see table above).

## Known limitations (internal/architectural — see MANUAL.md for user-facing ones)

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
