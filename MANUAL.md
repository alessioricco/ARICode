# Manual

The complete usage guide for the coding-agent harness: setup, configuration,
CLI reference, execution modes, projects, custom tools, and known gaps.

For the build plan and architecture rationale, see `docs/SPEC.md`. For a
short project overview, see `README.md`. This file is the operational
reference — how to actually run and configure the thing.

> **Maintenance note (for whoever/whatever is developing this repo):** this
> manual must be kept in sync with the code. Any change that adds or changes
> a CLI flag, an env var, an execution mode, a custom tool, or a known
> limitation should update the relevant section here in the same change —
> see `CLAUDE.md`.

## Contents

- [Setup](#setup)
- [Configuration reference](#configuration-reference)
- [CLI reference](#cli-reference)
- [Server mode (HTTP/WebSocket)](#server-mode-httpwebsocket)
- [Projects: one subfolder per generated project](#projects-one-subfolder-per-generated-project)
- [Skills](#skills)
- [Execution modes](#execution-modes)
- [Switching LLM provider / model](#switching-llm-provider--model)
- [Custom tools](#custom-tools)
- [Testing](#testing)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for the virtual
environment and dependencies. Run every command through `uv run`, which uses
`.venv` automatically — never install into system Python.

```bash
cp .env.example .env
# edit .env: set LLM_MODEL and LLM_API_KEY (see "Switching LLM provider / model")

uv pip install -e ".[dev]"        # harness + openhands-sdk/openhands-tools + pytest/ruff
uv run pytest -q                  # should pass with no network/keys required
```

Only needed for `HARNESS_EXECUTION=docker`:

```bash
uv pip install -e ".[sandbox]"    # adds openhands-workspace, openhands-agent-server
```

Only needed for [server mode](#server-mode-httpwebsocket):

```bash
uv pip install -e ".[server]"     # adds fastapi, uvicorn, httpx
```

`openhands-sdk` and `openhands-tools` are a matched set — always
install/upgrade both together at the same version (see `pyproject.toml`).

## Configuration reference

All configuration is env vars, loaded from `.env` by `src/harness/config.py`
(`load_config()`). `.env` is git-ignored; `.env.example` is the checked-in
template with every variable documented inline.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_MODEL` | *(required)* | LiteLLM-style model id, e.g. `anthropic/claude-sonnet-4-5-20250929`, `openai/gpt-4o`, `ollama/llama3`. The **only** thing you change to switch provider. |
| `LLM_API_KEY` | *(required unless `LLM_BASE_URL` set)* | Key for whichever provider `LLM_MODEL` selects. |
| `LLM_BASE_URL` | *(empty)* | Only for local/self-hosted models (e.g. `http://localhost:11434`). Leave the line with no value and no trailing text — see [Troubleshooting](#troubleshooting). |
| `HARNESS_WORKSPACE` | `.` | Working directory the agent operates in when `--project` is not used. |
| `HARNESS_MAX_ITERATIONS` | `50` | Safety cap on the agent loop. |
| `HARNESS_CONFIRM_MODE` | `never` | `never` \| `always` (pause before each tool call — policy not yet wired to an actual confirmation gate; see `agent.py`). |
| `HARNESS_EXECUTION` | `local` | `local` \| `docker` — see [Execution modes](#execution-modes). |
| `HARNESS_PROJECTS_DIR` | `./projects` | Root folder for generated projects; `--project NAME` resolves to `HARNESS_PROJECTS_DIR/NAME`. |
| `HARNESS_SKILLS_DIR` | `./skills` | Shared skill catalog loaded into every agent's `AgentContext` — see [Skills](#skills). |
| `HARNESS_DOCKER_IMAGE` | `coding-agent-harness/agent-server:local` | Image used for `HARNESS_EXECUTION=docker`. Built automatically on first use. |
| `HARNESS_DOCKER_PLATFORM` | auto-detected from host arch | `linux/amd64` \| `linux/arm64`. Leave blank to auto-detect (arm64 on Apple Silicon, amd64 otherwise). |

## CLI reference

```bash
uv run python -m harness "<task>" [--execution {local,docker}] [--project NAME] [--agents-md TEXT]
```

(Also installed as a console script: `harness "<task>" ...`, once the package
is installed via `uv pip install -e .`.)

- `task` (positional, required) — the instruction given to the agent.
- `--execution {local,docker}` — overrides `HARNESS_EXECUTION` for this run only.
- `--project NAME` — overrides the workspace to `HARNESS_PROJECTS_DIR/NAME`
  (created if missing). See [Projects](#projects-one-subfolder-per-generated-project).
- `--agents-md TEXT` — writes `TEXT` as this project's `AGENTS.md`. Requires
  `--project`. See [Skills](#skills).

Exit code is `0` on success, `1` on a configuration error or a run-time error
(printed to stderr as `Configuration error: ...` / `Error: ...` — not a raw
traceback). The agent's final message is printed to stdout.

Examples:

```bash
# Simplest form — uses HARNESS_WORKSPACE, whatever HARNESS_EXECUTION is set to
uv run python -m harness "Refactor utils.py to remove duplication, then run the tests."

# A specific generated project, run locally
uv run python -m harness "Scaffold a FastAPI service with a /health endpoint." --project my-api

# Same project, containerized
uv run python -m harness "Add a /version endpoint." --project my-api --execution docker
```

## Server mode (HTTP/WebSocket)

`src/harness/server.py` exposes the same `run_task`/`stream_task` machinery
the CLI uses as an HTTP/WebSocket API, so an IDE plugin, dashboard, or script
can drive the harness over the network instead of shelling out to the CLI.
It's a thin interface on top of the existing agent loop — not a second
implementation of it (same golden rule as `cli.py`: config, custom tools, an
interface, and policy are ours; the SDK owns the loop).

Requires the `server` extra (`uv pip install -e ".[server]"`); importing
`harness.server` without it raises a clear `RuntimeError` rather than an
import failure.

```bash
uv run harness-server --host 127.0.0.1 --port 8000
# or: uv run python -m harness.server --port 8000
```

### `GET /health`

Returns `{"status": "ok"}`.

Two ways to run a task: submit-and-poll (`POST` + `GET`), for when a task
might take too long to hold an HTTP connection open, or the client just wants
to check in later; and streaming (`WS`), for live progress in the same
connection. Pick whichever fits the client.

### `POST /tasks` — submit, returns immediately

Starts the task in a background thread and returns right away — it does
**not** wait for the agent to finish (code generation can take anywhere from
seconds to minutes):

```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"task": "Create HELLO.txt with the line: hi.", "project": "my-api", "execution": "docker"}'
# -> 202 {"task_id": "f4fe313c-...", "status": "running"}
```

Request body: `task` (required), `project` (optional, same meaning as CLI
`--project`), `execution` (optional, same meaning as CLI `--execution`),
`agents_md` (optional, same meaning as CLI `--agents-md` — requires `project`
in the same request). Response (`202 Accepted`): `{"task_id": "...", "status":
"..."}`. Config errors — including `agents_md` without `project` — are
validated synchronously before a task is even created, so those still come
back as `400` immediately — only a run-time failure (once the agent is
actually working) shows up later as an async `"failed"` status via
`GET /tasks/{task_id}`, not as an HTTP error on this call.

### `GET /tasks/{task_id}` — poll status/result

```bash
curl http://127.0.0.1:8000/tasks/f4fe313c-...
```

```json
{
  "task_id": "f4fe313c-...",
  "status": "running",
  "final_message": null,
  "messages": [],
  "error": null
}
```

`status` is `pending` → `running` → `completed` | `failed`. `messages`
accumulates as the agent works (so polling mid-run shows partial progress,
not just the final result — see `_TaskRecord` in `server.py`), each entry
full-fidelity (`Message.model_dump(mode="json")`). `final_message` is set
once `status == "completed"`; `error` is set once `status == "failed"`.
`404` for an unknown `task_id`.

**In-memory only:** the task registry lives in the server process's memory —
restarting the server loses all task history, and it isn't shared across
multiple server processes/workers. Fine for a single long-running server
process; would need a real store (Redis, a DB) to survive restarts or scale
horizontally. Records are also never purged — long-running servers will
accumulate them for now (see [Known limitations](#known-limitations)).

### `WS /tasks/stream` — live streaming, single connection

Same inputs, sent as the first WebSocket message, but pushes each message to
the client as the agent produces it, over the connection that's already open
— no polling needed:

```python
import json
from websockets.sync.client import connect

with connect("ws://127.0.0.1:8000/tasks/stream") as ws:
    ws.send(json.dumps({"task": "...", "project": "my-api"}))
    while True:
        print(json.loads(ws.recv()))   # raises ConnectionClosedOK when the run finishes
```

Each frame is `{"type": "message", ...Message.model_dump()}` or
`{"type": "error", "detail": "..."}`; the server closes the socket once the
run finishes (normal close, code 1000) or after sending an error. The blocking
`conversation.run()` call runs in a background thread per connection, bridged
to the async WebSocket loop via a queue — this is why `server.py` needs
`threading`/`queue`, not just `asyncio`. This same thread-plus-queue pattern
is what `POST`/`GET` build on too, just with the queue's contents parked in
the `_TaskRecord` instead of pushed straight to a socket.

**Status:** verified live — real `POST /tasks` returned in well under a
second with `status: "running"` while the agent was still working; polling
`GET /tasks/{task_id}` showed the real transition through to `"completed"`
with the final message and full history; a real WebSocket connection
streamed all 8 messages of a separate multi-step task live, then closed
cleanly.

## Projects: one subfolder per generated project

`--project NAME` puts the agent's workspace at `HARNESS_PROJECTS_DIR/NAME`
(default `./projects/NAME`), creating the directory if it doesn't exist yet.
Re-running with the same `--project NAME` reuses the same folder, so a
project accumulates across multiple task invocations; a different name gets
its own separate folder. `projects/` is git-ignored — nothing generated there
is committed to this repo automatically.

Omitting `--project` falls back to plain `HARNESS_WORKSPACE` (no per-project
folder) — this is what the e2e smoke test (`tests/test_runner.py`) uses, and
is fine for one-off tasks that don't need to be revisited.

The resolved workspace is always made absolute before being handed to the
agent (`os.path.abspath` in `cli.py`) — this matters because the agent's
`file_editor` tool requires absolute paths and does not resolve relative ones
against the workspace itself (see [Known limitations](#known-limitations)).

## Skills

Two distinct mechanisms — don't conflate them. "Microagents" in spec section
9's wording is the old OpenHands term for what the SDK now calls **Skills**
(unified with the cross-platform [agentskills.io](https://agentskills.io/specification)
spec — the same shape of idea as the Claude Code skills used to build this
project, by design). See `src/harness/skills.py` for the implementation and
`ROADMAP.md`'s decisions log for why this shape was chosen over the
alternatives that were considered.

### Shared skill catalog (`skills/`)

`HARNESS_SKILLS_DIR` (default `./skills`) is a library of **reusable**
knowledge — conventions worth writing once and applying to *any* project
where they're relevant, not facts about one specific project. Every skill is
loaded into every agent's `AgentContext` (`build_agent()` in `agent.py`); the
SDK matches each skill's own trigger against the task/conversation
automatically — there's no custom "which skill applies" logic in this
codebase, and no extra LLM call to decide relevance.

**File format** — a markdown file with YAML frontmatter (the "legacy
OpenHands" format; AgentSkills-standard `SKILL.md` directories also work, but
only one level deep — see the caveat below):

```markdown
---
name: pytest-conventions          # optional; derived from the file path if absent
triggers:
  - pytest
  - test
description: Conventions for writing pytest-based tests.
---

Markdown content here — this is what gets injected when the skill fires.
```

**Trigger types**, set via frontmatter, not code:

| Frontmatter | Trigger | Fires when |
|---|---|---|
| `triggers: [...]` | `KeywordTrigger` | one of the listed keywords appears in the task/conversation (whole-token, case-insensitive) |
| `paths: [...]` | `PathTrigger` | the agent touches a file matching one of the globs — a "rule", not model-invocable |
| *(neither)* | none (`repo` skill) | always active, injected unconditionally |

**Subfolders are purely organizational** — `skills/testing/`, `skills/web/`,
etc. exist for human classification only. `load_skill_catalog()` doesn't walk
directories itself; the SDK's `load_skills_from_dir()` finds `.md` files
recursively (`rglob("*.md")`) under the hood, so nesting costs nothing extra.
The one caveat: `SKILL.md`-format AgentSkills directories are only detected
one level deep (an SDK constraint) — use the flat `.md`-with-frontmatter
format for anything inside a category subfolder.

This repo ships four example skills to prove the wiring and as a starting
point: `skills/testing/pytest-conventions.md`, `skills/git/commit-conventions.md`
(both `KeywordTrigger`), `skills/python-web/fastapi-conventions.md`
(`KeywordTrigger`), and `skills/python-web/pin-dependencies.md` (`PathTrigger`
— fires on `pyproject.toml`/`requirements*.txt`, not on task text). Add more
the same way; no registration step beyond dropping the file in `skills/`.

### Per-project context (`--agents-md` / `agents_md`)

The other mechanism: **persistent, project-specific facts** ("this project
uses FastAPI + Poetry"), not reusable expertise. Since this harness is driven
by a task, not a human editing files in a project's subfolder between runs,
the caller supplies this content as a parameter and the harness writes it:

```bash
uv run python -m harness "..." --project my-api --agents-md "This project uses FastAPI + Poetry."
```

```bash
curl -X POST http://127.0.0.1:8000/tasks -H "Content-Type: application/json" \
  -d '{"task": "...", "project": "my-api", "agents_md": "This project uses FastAPI + Poetry."}'
```

This writes (overwrites) `<project_dir>/AGENTS.md` — a filename the SDK's
`load_project_skills()` already recognizes as a "third-party" instruction
file with no frontmatter required, so it applies to this task and every
future task against the same project directory, with zero extra wiring.
`--agents-md`/`agents_md` **requires** `--project`/`project` — supplying it
without a named project is rejected (`400` / CLI exit 1) rather than silently
writing into whatever `HARNESS_WORKSPACE` happens to be, which could be this
repo's own working directory.

**Status:** both mechanisms verified live — a task containing "pytest"
correctly triggered `pytest-conventions` (confirmed via the SDK's own log
line, `Skill 'pytest-conventions' triggered by keyword 'pytest'`, and the
model's response followed the injected conventions); a real `--agents-md`
call wrote `AGENTS.md` to the project folder and a subsequent task's
`<REPO_CONTEXT>` block contained it verbatim, reflected in the model's answer.

## Execution modes

`HARNESS_EXECUTION` (or `--execution`) selects where the agent's tools
actually run. The dispatch lives entirely in `src/harness/workspace.py`
(`build_workspace(cfg)`) — this is the one place execution backends are
chosen, so a future backend (ECS, EC2, ...) is one new branch there plus one
new `HARNESS_EXECUTION` value, not changes to `agent.py`, `runner.py`, or
`cli.py`. Only `local` and `docker` exist today.

### `local`

Tools run in this process, directly against the workspace directory on disk.
No extra setup beyond the base install.

### `docker`

The agent loop and its tools run inside a container instead of this process.

- Requires Docker running locally and the `sandbox` extra installed
  (`uv pip install -e ".[sandbox]"`).
- Built from `docker/agent-server.Dockerfile`, which layers our custom tool
  source onto the published `ghcr.io/openhands/agent-server` image.
- `src/harness/workspace.py` builds the image automatically the first time
  it's needed (`docker build ...`, logged to stdout) and reuses it after
  that. Rebuild manually after changing any custom tool:

  ```bash
  docker build -f docker/agent-server.Dockerfile -t coding-agent-harness/agent-server:local .
  ```

- The resolved workspace directory is bind-mounted into the container at
  `/workspace`, so files the agent creates land in the same host folder
  regardless of execution mode — `--project my-api --execution docker` writes
  to `./projects/my-api` on the host exactly like `--execution local` does.
- The container is torn down automatically after the run (success or
  failure) — `runner.py` uses `DockerWorkspace` as a context manager. No
  manual cleanup needed; `docker ps` should never show a leftover
  `agent-server-*` container after a run completes or errors out.

#### Why custom tools need their own image layer

`register_tool()` only affects the process that calls it. Under `local`
execution that's this process; under `docker` execution the agent loop runs
inside the container's own separate agent-server process, which never
otherwise imports our custom tool code — it only knows the SDK's built-in
tools out of the box. `docker/agent-server.Dockerfile` solves this by:

1. Copying `src/harness/custom_tools/` into the image.
2. Setting `OH_EXTRA_PYTHON_PATH` so that code is importable (a plain
   `PYTHONPATH` doesn't work here — the container's entrypoint is a
   PyInstaller-frozen binary that doesn't honor it, which is exactly what
   `OH_EXTRA_PYTHON_PATH` + the SDK's `--import-modules` flag exist for).
3. Overriding `ENTRYPOINT` to pass `--import-modules harness.custom_tools`,
   so the container imports (and thereby registers) every tool in that
   package at startup.

This is also why adding a new custom tool for use under Docker needs two
registration points, not one — see [Custom tools](#custom-tools).

## Switching LLM provider / model

Edit **only** `LLM_MODEL` in `.env` — no code changes, ever. Nothing in
`src/` hardcodes a model, provider, or base URL (verified via
`grep -rniE "gpt-|claude-|anthropic/|openai/|gemini/|ollama/" src/` — the only
hits are example strings in error messages and docstrings).

- `anthropic/claude-...` — Anthropic
- `openai/gpt-4o` — OpenAI
- `gemini/gemini-...` — Google
- `ollama/llama3` (+ `LLM_BASE_URL=http://localhost:11434`) — local model
- `openhands/claude-...` — OpenHands proxy

Model IDs drift over time; verify current strings against the provider +
LiteLLM docs if one errors.

**Status:** verified live against `openai/gpt-4o` (Milestones 2, 4, 5, and
the Docker execution work were all proven against this provider). The actual
live proof of *swapping* to a second provider (spec Milestone 3) has not been
run yet — it needs a second provider's key or a local model endpoint, which
wasn't available at the time. The code path is provider-agnostic by
construction; what's missing is the live confirmation run, not an
implementation gap.

## Custom tools

Custom tools follow the SDK's Action / Observation / Executor pattern and
live in `src/harness/custom_tools/`:

- `custom_tools/example_tool.py` — template, not wired into the agent.
- `custom_tools/run_tests_tool.py` — real tool (`run_tests`): runs this
  project's pytest suite (or a single file/node id) as a subprocess and
  returns structured pass/fail/error counts, a one-line summary, and parsed
  `FAILED`/`ERROR` node ids, instead of making the agent shell out through
  the terminal tool and hand-parse raw output.

To add a new one (in Claude Code: `/add-tool <description>`; see
`docs/SPEC.md` section 7 for the pattern):

1. Create `custom_tools/<name>_tool.py`: an `Action` subclass (validated
   inputs), an `Observation` subclass (structured output), a `ToolExecutor`
   (the logic), and a `ToolDefinition` subclass with a `create(cls,
   conv_state, **params)` classmethod. Call `register_tool(YourTool.name,
   YourTool)` **at module level**, not just inside a factory function — this
   is what makes the tool self-register on import, which both `local`
   execution and the Docker image's `--import-modules` mechanism depend on.
2. Register it in `build_tools()` (`src/harness/tools.py`) so the agent
   picks it up under `local` execution.
3. Import it from `custom_tools/__init__.py` (add `from . import
   <name>_tool`) so it's also registered under `docker` execution, then
   rebuild the Docker image (see [Execution modes](#execution-modes)).
4. Add `tests/custom_tools/test_<name>_tool.py`: construct the `Action`, call
   the executor directly, assert on the `Observation` — no LLM, no network.

## Testing

```bash
uv run pytest -q
```

- `tests/test_config.py` — env parsing, no SDK, no network.
- `tests/test_skills.py` — `load_skill_catalog()` (against a temp directory
  with nested subfolders) and `write_project_context()`, no LLM.
- `tests/custom_tools/test_*.py` — tool executors called directly, no LLM.
- `tests/test_workspace.py` — `build_workspace()` dispatch; the Docker branch
  monkeypatches `subprocess` and `DockerWorkspace`, so this suite never
  touches a real Docker daemon. Skips cleanly
  (`pytest.importorskip("openhands.workspace")`) when the `sandbox` extra
  isn't installed.
- `tests/test_cli.py` — argument parsing and control flow; `load_config`/
  `run_task` are monkeypatched, no LLM, no Docker.
- `tests/test_server.py` — REST/WebSocket routes via FastAPI's `TestClient`;
  `load_config`/`run_task`/`stream_task` are monkeypatched, no LLM. Skips
  cleanly (`pytest.importorskip("fastapi")`) when the `server` extra isn't
  installed.
- `tests/test_runner.py` — real end-to-end smoke test. **Skips cleanly** when
  `LLM_MODEL`/`LLM_API_KEY` aren't configured; when they are, it makes one
  real, cheap LLM call and asserts a file was actually created. This means a
  full `pytest -q` run in a repo with a working `.env` makes a real API call
  every time — that's intentional (spec section 10), not a leak.

## Known limitations

- **`file_editor` requires absolute paths.** It does not resolve a relative
  path like `"HELLO.txt"` against the workspace directory — the model has to
  supply (or discover, e.g. via the terminal tool) a full absolute path.
  `--project` mitigates this by always resolving the workspace to an absolute
  path, but the task text or the model's own exploration still needs to
  arrive at the right absolute file path.
- **`run_tests` assumes a pytest-based project with pytest installed.**
  True for this repo's own suite under `local` execution. Under `docker`
  execution, `pytest` is not installed in the image, and a `--project`
  workspace holds newly generated software, not this repo — so `run_tests`
  isn't currently meaningful there. Making it generic (detect the project's
  actual test runner, install its dependencies) is unscoped.
- **`HARNESS_CONFIRM_MODE=always`** is parsed and validated but not yet wired
  to an actual confirmation gate in `agent.py` — the SDK's confirmation
  policy API hasn't been verified against this SDK version yet
  (`/verify-sdk` first).
- **Only `local` and `docker` execution exist.** ECS/EC2 (mentioned as a
  future direction) are not implemented; `workspace.py` is structured so
  adding one is additive, not a rewrite.
- **Provider-swap live proof (spec Milestone 3) hasn't been run** — see
  [Switching LLM provider / model](#switching-llm-provider--model).
- **Server mode's task registry is in-memory, per-process, and unbounded.**
  `POST`/`GET /tasks` state is lost on restart, not shared across multiple
  server processes, and completed/failed records are never purged — see
  [Server mode](#server-mode-httpwebsocket).

## Troubleshooting

**"LLM_MODEL is required" / "LLM_API_KEY is required"** — `.env` is missing
or a required value is blank. Copy `.env.example` and fill in `LLM_MODEL` +
`LLM_API_KEY` (or `LLM_BASE_URL` for a keyless local model).

**`LLM_BASE_URL` gets read as a literal `# comment` string** — `python-dotenv`
does not strip a trailing `# comment` from a line whose value is otherwise
blank (`KEY=                    # comment` keeps the comment as the value; a
non-blank value before the comment strips fine). Keep the line as `LLM_BASE_URL=`
with nothing else on it if you're not using a local model.

**Agent writes to the wrong path / "Read-only file system" errors under
`local` execution** — the model guessed a container-style absolute path
(e.g. `/HELLO.txt`) that doesn't exist as a real, writable directory on your
host. Use `--project NAME` so the workspace is a real absolute path under
`HARNESS_PROJECTS_DIR`, and/or phrase the task with an explicit absolute path.

**"HARNESS_EXECUTION=docker requires the 'sandbox' extra"** — run
`uv pip install -e ".[sandbox]"`.

**Docker execution hangs or fails to become healthy** — confirm Docker
Desktop/daemon is actually running (`docker ps`), and that
`HARNESS_DOCKER_PLATFORM` matches your host (leave it blank to auto-detect;
forcing the wrong platform runs the image under emulation, which is slow, or
may fail outright).

**A custom tool works under `local` but not `docker`** — it almost certainly
isn't imported from `custom_tools/__init__.py`, or the image wasn't rebuilt
after adding it. See [Custom tools](#custom-tools).
