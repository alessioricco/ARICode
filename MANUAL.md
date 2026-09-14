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
| `HARNESS_DOCKER_IMAGE` | `coding-agent-harness/agent-server:local` | Image used for `HARNESS_EXECUTION=docker`. Built automatically on first use. |
| `HARNESS_DOCKER_PLATFORM` | auto-detected from host arch | `linux/amd64` \| `linux/arm64`. Leave blank to auto-detect (arm64 on Apple Silicon, amd64 otherwise). |

## CLI reference

```bash
uv run python -m harness "<task>" [--execution {local,docker}] [--project NAME]
```

(Also installed as a console script: `harness "<task>" ...`, once the package
is installed via `uv pip install -e .`.)

- `task` (positional, required) — the instruction given to the agent.
- `--execution {local,docker}` — overrides `HARNESS_EXECUTION` for this run only.
- `--project NAME` — overrides the workspace to `HARNESS_PROJECTS_DIR/NAME`
  (created if missing). See [Projects](#projects-one-subfolder-per-generated-project).

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

### `POST /tasks`

Runs a task synchronously (blocks until the agent finishes) and returns the
full result:

```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"task": "Create HELLO.txt with the line: hi.", "project": "my-api", "execution": "docker"}'
```

Request body: `task` (required), `project` (optional, same meaning as CLI
`--project`), `execution` (optional, same meaning as CLI `--execution`).
Response: `{"final_message": "...", "messages": [...]}` — `messages` is every
captured `Message`, full-fidelity (`model_dump(mode="json")`), not just text.
`400` on a config error, `500` on a run-time error — both with a `detail`
string, no raw traceback leaked to the client.

### `WS /tasks/stream`

Same inputs, sent as the first WebSocket message, but streams each message
as the agent produces it instead of blocking for the whole run:

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
`threading`/`queue`, not just `asyncio`.

**Status:** verified live — real REST call created a file via the live LLM
and returned the full message history; real WebSocket connection streamed
all 8 messages of a multi-step task live, then closed cleanly.

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
