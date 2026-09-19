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
| `LLM_REASONING_EFFORT` | *(empty — SDK default `high` applies)* | Provider-neutral reasoning effort, passed straight through to the SDK's `LLM.reasoning_effort` (LiteLLM translates it per-provider). Common values: `none` \| `minimal` \| `low` \| `medium` \| `high` \| `xhigh` \| `max` — not validated against a fixed list, since the SDK accepts forward-compatible provider values too. |
| `HARNESS_WORKSPACE` | `.` | Working directory the agent operates in when `--project` is not used. |
| `HARNESS_MAX_ITERATIONS` | `50` | Safety cap on the agent loop. |
| `HARNESS_CONFIRM_MODE` | `never` | `never` \| `always` (pause before each tool call for approval — see [Confirmation mode](#confirmation-mode)). |
| `HARNESS_EXECUTION` | `local` | `local` \| `docker` — see [Execution modes](#execution-modes). |
| `HARNESS_PROJECTS_DIR` | `./projects` | Root folder for generated projects; `--project NAME` resolves to `HARNESS_PROJECTS_DIR/NAME`. |
| `HARNESS_SKILLS_DIR` | `./skills` | Shared skill catalog loaded into every agent's `AgentContext` — see [Skills](#skills). |
| `HARNESS_DOCKER_IMAGE` | `coding-agent-harness/agent-server:local` | Image used for `HARNESS_EXECUTION=docker`. Built automatically on first use. |
| `HARNESS_DOCKER_PLATFORM` | auto-detected from host arch | `linux/amd64` \| `linux/arm64`. Leave blank to auto-detect (arm64 on Apple Silicon, amd64 otherwise). |
| `HARNESS_VERIFY_TESTS` | `always` | `always` \| `never` — after the agent finishes, re-run the project's own tests and, if they fail, send the real failure back and let the agent retry. See [Test verification](#test-verification). |
| `HARNESS_MAX_VERIFY_RETRIES` | `2` | How many automated fix-and-retry cycles `HARNESS_VERIFY_TESTS=always` allows before giving up. |
| `HARNESS_MAX_TASK_SECONDS` | `1800` | Shared wall-clock budget (seconds) for one whole task — the initial run plus every task_tracker/verification retry combined, not just a single `conversation.run()` call. See [Task budget](#task-budget). |

## CLI reference

```bash
uv run python -m harness "<task>" [--execution {local,docker}] [--project NAME] \
    [--agents-md TEXT] [--model MODEL] [--api-key KEY] [--base-url URL] \
    [--reasoning-effort LEVEL] [--require-verification] \
    [--acceptance-checks JSON_OR_FILE]
```

(Also installed as a console script: `harness "<task>" ...`, once the package
is installed via `uv pip install -e .`.)

- `task` (positional, required) — the instruction given to the agent, **or a
  reference to it**: an http(s) URL (fetched) or a path to an existing local
  file (read), checked in that order before falling back to literal text.
  See `resolve_task_source()` in `cli.py`. Server mode's `task` field (REST/
  WS) does **not** do this resolution — it's CLI-only; API callers already
  have the content in hand or can fetch/read it themselves before the
  request.
- `--execution {local,docker}` — overrides `HARNESS_EXECUTION` for this run only.
- `--project NAME` — overrides the workspace to `HARNESS_PROJECTS_DIR/NAME`
  (created if missing). See [Projects](#projects-one-subfolder-per-generated-project).
- `--agents-md TEXT` — writes `TEXT` as this project's `AGENTS.md`. Requires
  `--project`. See [Skills](#skills).
- `--model MODEL` / `--api-key KEY` / `--base-url URL` / `--reasoning-effort
  LEVEL` — override `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` /
  `LLM_REASONING_EFFORT` for this run only, without touching `.env`. Useful
  for running the same task against different models/providers, or the same
  model at different reasoning-effort levels, to compare results. Any
  combination may be given; an omitted flag keeps `.env`'s value. See
  [Switching LLM provider / model](#switching-llm-provider--model).
- `--require-verification` — off by default. Treats an `inconclusive`
  verification result (nothing runnable confirmed the software works — an
  unknown project type, a missing required tool, a project with no tests
  yet, ...) as a failure too: nonzero exit, same as
  `retry_exhausted`/`timed_out`/`stuck`/etc. Without this flag,
  `inconclusive` exits `0` — the current default, kept for backward
  compatibility, since "nothing was proven broken" isn't the same claim as
  "something is broken." A CI pipeline or script that wants a hard
  pass/fail signal on the exit code alone (rather than parsing the
  `Verification:` line or `TaskOutcome.verification_state`) should pass
  this flag.
- `--acceptance-checks JSON_OR_FILE` — optional, opt-in machine-checkable
  acceptance criteria for this specific task, on top of (not instead of)
  the project's own tests/build. See [Acceptance
  checks](#acceptance-checks) below.

The agent's final message is printed to stdout, followed by a
`Verification: <state>` line (and any limitation notes under it) — see
[Test verification](#test-verification) for what each state means. Exit code
is `1` on a configuration error, a run-time error (printed to stderr as
`Configuration error: ...` / `Error: ...` — not a raw traceback), or a
`retry_exhausted`/`no_progress`/`timed_out`/`incomplete`/`confirmation_required`/
`budget_exhausted`/`acceptance_failed`/`stuck` verification outcome; `0`
for `verified` and `inconclusive` (the latter isn't an error by default —
nothing was proven broken — but it's still printed so it isn't mistaken
for a confirmed pass; pass `--require-verification` to make it nonzero
too).

Examples:

```bash
# Simplest form — uses HARNESS_WORKSPACE, whatever HARNESS_EXECUTION is set to
uv run python -m harness "Refactor utils.py to remove duplication, then run the tests."

# A specific generated project, run locally
uv run python -m harness "Scaffold a FastAPI service with a /health endpoint." --project my-api

# Same project, containerized
uv run python -m harness "Add a /version endpoint." --project my-api --execution docker

# Task text from a local file
uv run python -m harness ./tasks/build-the-thing.md --project my-api

# Task text fetched from a URL
uv run python -m harness https://example.com/tasks/build-the-thing.md --project my-api

# Compare two models on the exact same task, without touching .env
uv run python -m harness "Add input validation to the signup form." --project my-api \
    --model anthropic/claude-sonnet-4-5-20250929
uv run python -m harness "Add input validation to the signup form." --project my-api \
    --model openai/gpt-4o --api-key "$OPENAI_API_KEY"

# Compare reasoning-effort levels on the same model, without touching .env
uv run python -m harness "Fix the race condition in the queue worker." --project my-api \
    --reasoning-effort low
uv run python -m harness "Fix the race condition in the queue worker." --project my-api \
    --reasoning-effort xhigh
```

### Task source resolution

`resolve_task_source()` checks, in order: is it an http(s) URL (fetched with
a 15s timeout)? Is it an existing local file (read as UTF-8)? If neither,
the value is used exactly as given, as literal task text. Only `http`/`https`
schemes are ever fetched — this is also what keeps a `file://...` value from
ever reaching `urlopen` (it just falls through to the literal-text case,
since it won't match an existing path either). Whitespace-only content from
a file or URL is rejected as a clear error rather than silently running an
empty task; a fetch/read failure is reported the same way (`Error: ...`,
exit 1), not a raw traceback.

**Known edge case:** a literal task description that happens to exactly
match an existing filename in the current directory will be read as that
file's content instead of used literally — there's no override flag to force
literal interpretation. Unlikely in practice (task descriptions are rarely
also valid, existing filenames), but worth knowing.

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
in the same request), `model` / `api_key` / `base_url` / `reasoning_effort`
(all optional, same meaning as CLI `--model` / `--api-key` / `--base-url` /
`--reasoning-effort` — override `LLM_MODEL`/`LLM_API_KEY`/`LLM_BASE_URL`/
`LLM_REASONING_EFFORT` for this request only, without touching `.env`),
`require_verification` (optional, boolean, default `false` — same meaning
as CLI `--require-verification`; see below), `acceptance_checks` (optional,
a JSON array of check objects, same shape and meaning as CLI
`--acceptance-checks` — see [Acceptance checks](#acceptance-checks) below;
unlike the CLI, this is always inline JSON, never a file path).
Response (`202 Accepted`): `{"task_id": "...", "status":
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
  "verification_state": null,
  "completion_contract": null,
  "acceptance_results": null,
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

**`status == "completed"` only ever means the run didn't raise an
exception — it is not proof the work is correct.** Check
`verification_state` for that: one of `"verified"`, `"inconclusive"`,
`"retry_exhausted"`, `"no_progress"`, `"timed_out"`, `"incomplete"`,
`"confirmation_required"`, `"budget_exhausted"`, `"acceptance_failed"`, or
`"stuck"` (see [Test verification](#test-verification) for what each means
and when it's set),
populated once `status` reaches `"completed"` (or `"failed"` — see
`require_verification` below). `completion_contract` is the structured
record of what was actually checked: `{"goal", "acceptance_criteria",
"verification_checks", "limitations"}` (same shape as `runner.py`'s
`CompletionContract`).

**`require_verification: true`** makes an `"inconclusive"` result end with
`status: "failed"` instead of `"completed"` (with `error` set to a message
explaining why), so a caller that only checks the coarse `status` field —
not `verification_state` — still gets an accurate pass/fail signal. Off by
default: `"inconclusive"` keeps `status: "completed"`, matching CLI's
default. Every other `verification_state` is unaffected either way — this
only changes the specific "nothing could be checked" case. The `WS
/tasks/stream` equivalent sends a `{"type": "error", ...}` event instead of
`{"type": "result", ...}` in the same situation.

**In-memory only:** the task registry lives in the server process's memory —
restarting the server loses all task history, and it isn't shared across
multiple server processes/workers. Fine for a single long-running server
process; would need a real store (Redis, a DB) to survive restarts or scale
horizontally. Records are also never purged — long-running servers will
accumulate them for now (see [Known limitations](#known-limitations)).

### `WS /tasks/stream` — live streaming, single connection

Same inputs (including `model`/`api_key`/`base_url`/`reasoning_effort`/
`require_verification`/`acceptance_checks`), sent as the first WebSocket
message, but pushes each message to the client as the agent produces it,
over the connection that's already open — no polling needed:

```python
import json
from websockets.sync.client import connect

with connect("ws://127.0.0.1:8000/tasks/stream") as ws:
    ws.send(json.dumps({"task": "...", "project": "my-api"}))
    while True:
        print(json.loads(ws.recv()))   # raises ConnectionClosedOK when the run finishes
```

Each frame is `{"type": "message", ...Message.model_dump()}`,
`{"type": "error", "detail": "..."}`, or — the last frame before the socket
closes on a successful run — `{"type": "result", "verification_state": ...,
"completion_contract": {...}, "acceptance_results": [...] | null}`, the
same terminal outcome `GET /tasks/{task_id}` exposes (see [Test
verification](#test-verification)).
With `require_verification: true`, an `"inconclusive"` outcome sends
`{"type": "error", "detail": "..."}` instead of a `"result"` frame — same
`require_verification` semantics as `POST /tasks`, just expressed as which
frame type arrives rather than which `status` value.
The server closes the socket once the run finishes (normal close, code 1000)
or after sending an error. The blocking
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

### `POST /v1/chat/completions` — OpenAI-compatible adapter

For tools that only know how to talk to an OpenAI-shaped endpoint (some IDE
integrations, chat UIs), not a replacement for the native API above — a
protocol translation layer over the same `run_task`/`stream_task`.

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Create HELLO.txt with the line: hi."}], "project": "my-api"}'
```

- `model` (required by the wire format, echoed back in the response) **does
  not select a provider** — the real model defaults to whatever `.env`'s
  `LLM_MODEL` says, and `model` isn't a safe stand-in for an explicit
  override since a strict OpenAI client's value there may not be a
  LiteLLM-style `"provider/model"` id at all.
- `messages`: every `system` message is concatenated as leading context; the
  **last** `user` message becomes the task. Prior `assistant` turns are
  **not replayed** — this harness's continuity story is the project's own
  workspace/skills (see [Skills](#skills)), not chat history, and there's no
  cheap way to resume a previous agent loop mid-conversation.
- `project` / `execution` — harness extensions, same meaning as the native
  API; ignored by strict OpenAI clients that don't send them. No
  `agents_md` field here — use the native `POST /tasks` for that.
- `llm_model` / `llm_api_key` / `llm_base_url` / `llm_reasoning_effort` —
  harness extensions, the actual per-request LLM overrides (same meaning as
  `POST /tasks`' `model`/`api_key`/`base_url`/`reasoning_effort`). Kept
  separate from the wire-mandated `model` field above precisely because that
  field can't be trusted to hold a real provider/model id — see
  [Switching LLM provider / model](#switching-llm-provider--model).
- `stream: true` — Server-Sent Events instead of one blocking JSON response,
  using the same background-thread-plus-queue pattern as `WS /tasks/stream`.
- `GET /v1/models` reflects the real configured model (`{"data": [{"id":
  "<cfg.model>", ...}]}`) — informative, not a real selectable list.

**A real, live-caught bug worth understanding, not just a fact:** the
obvious-looking way to extract "what did the agent say" is to filter
messages to `role == "assistant"`. That's wrong for this SDK — confirmed by
inspecting real output, not assumed: an `assistant`-role turn that makes a
tool call has *empty* `content` (the call itself lives in `tool_calls`); the
human-readable text — including the final "finish" message — arrives as a
`tool`-role message's content instead. Filtering to `role == "assistant"`
silently drops everything, including the final answer, and returns an empty
string with `finish_reason: "stop"` (no error — the request "succeeds" with
nothing in it, so a shallow test asserting exit code / status alone would
have missed this). `_narrative_texts()` in `server.py` instead excludes only
`system` and `user` roles, so both `assistant` text turns (when they occur)
and `tool` result text (including the finish message) are included, matching
what a live run's full message list actually looks like — verified directly
via `POST /tasks`, not inferred.

**Status:** verified live — both non-streaming and streaming real calls
against the live LLM produce the correct final narrative text (confirmed
only after finding and fixing the role-filtering bug above via a live call
that came back with empty content despite the underlying task completing
successfully).

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

`NAME` must be a relative path with no `..` segment, and its resolved,
symlink-followed location must stay inside `HARNESS_PROJECTS_DIR` — both
`--project` and `POST /tasks`'s `project` field go through the same check
(`config.py`'s `resolve_project_dir()`) before the directory is created or
used. An absolute name (`--project /etc/cron.d`), a `..`-traversal attempt,
or a project name that resolves through an existing symlink to somewhere
outside `HARNESS_PROJECTS_DIR` is rejected with a clear error instead of
silently redirecting the agent's entire workspace — including its terminal
and file-editor tools — to an arbitrary path on the host.

### README.md

Every task instructs the agent (via `agent.py`'s system-prompt suffix, see
[Known limitations](#known-limitations)) to leave a `README.md` at the
project root with concrete install/run instructions for whatever it built,
before it finishes — not just when a task explicitly asks for one. This is a
prompt-level instruction, not something the harness generates or enforces
itself: the harness has no way to know how to run arbitrary generated code,
only the agent that built it does. Since it's a soft instruction, a given run
can still finish without one — check the project folder if in doubt.

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

**Trigger types**, set via frontmatter, not code — this applies to the
legacy `.md`-with-frontmatter format:

| Frontmatter | Trigger | Fires when |
|---|---|---|
| `triggers: [...]` | `KeywordTrigger` | one of the listed keywords appears in the task/conversation (whole-token, case-insensitive) |
| `paths: [...]` | `PathTrigger` | the agent touches a file matching one of the globs — a "rule", not model-invocable |
| *(neither)* | none (`repo` skill) | always active, injected unconditionally |

**`SKILL.md`-format skills work differently — model-invoked, not
deterministically triggered.** There's no `triggers:`/`paths:` frontmatter
for this format; instead the SDK lists every such skill's `name` +
`description` in an `<available_skills>` menu injected into every run's
system prompt, and auto-attaches an `invoke_skill` tool the model can call
by name to load that skill's full body — confirmed by reading the SDK's own
`context/prompts/sections/dynamic.py` and
`tool/builtins/invoke_skill.py`, not assumed. This means discoverability is
automatic and unconditional (the menu appears every run, regardless of task
phrasing), but *use* is a model judgment call, not a deterministic match —
the model can see a skill listed and still not invoke it.

**A legacy skill with a trigger is also listed in that same
`<available_skills>` menu — but unlike `SKILL.md`, its content is injected
automatically the moment the trigger fires, with no `invoke_skill` call
needed.** Confirmed by reading the SDK's `Skill` docstring in
`openhands/sdk/skills/skill.py` directly: "Legacy OpenHands format: With
triggers: Listed in `<available_skills>`, content injected on trigger." This
is why the eight lifecycle skills below (see "Lifecycle skills") use
`triggers:`/`paths:` rather than the `SKILL.md` format the earlier SDLC
skill set used — a `KeywordTrigger`/`PathTrigger` match is real,
code-enforced discoverability *and* injection, not a hope the model
remembers to invoke something. See ROADMAP.md's decisions log for the full
reasoning behind that switch.

**Subfolders are purely organizational** — `skills/testing/`, `skills/web/`,
etc. exist for human classification only. `load_skill_catalog()` doesn't walk
directories itself; the SDK's `load_skills_from_dir()` finds `.md` files
recursively (`rglob("*.md")`) under the hood, so nesting costs nothing extra.
The one caveat: `SKILL.md`-format AgentSkills directories are only detected
one level deep (an SDK constraint) — use the flat `.md`-with-frontmatter
format for anything inside a category subfolder.

This repo ships four example skills (legacy-format) to prove the wiring and
as a starting point: `skills/testing/pytest-conventions.md`,
`skills/git/commit-conventions.md` (both `KeywordTrigger`),
`skills/python-web/fastapi-conventions.md` (`KeywordTrigger`), and
`skills/python-web/pin-dependencies.md` (`PathTrigger` — fires on
`pyproject.toml`/`requirements*.txt`, not on task text). Add more the same
way; no registration step beyond dropping the file in `skills/`.

It also ships three `SKILL.md`-format skills sourced from
[anthropics/skills](https://github.com/anthropics/skills) (MIT-licensed):
`skills/frontend-design/`, `skills/webapp-testing/`, and
`skills/web-artifacts-builder/` — model-invoked as described above, one
level deep per the AgentSkills depth caveat.

### Lifecycle skills (`skills/lifecycle/`)

Eight legacy-format, keyword/path-triggered skills covering the software
lifecycle end to end — requirements through release — deliberately
**language- and framework-independent**: none of them assume Python, Node,
or any other stack, and ecosystem-specific conventions stay in their own
separate skills (`skills/python-web/`, `skills/testing/pytest-conventions.md`,
etc.), not mixed into these. Each is short (15-30 lines) and triggers on
task language relevant to its own lifecycle point — not one large,
always-active SDLC prompt.

| Skill | Trigger keywords | Covers |
|---|---|---|
| `repository-discovery` | `implement`, `build`, `add`, `create`, `refactor`, `migrate`, `integrate`, `scaffold`, `inspect`, `explore` | Identify the project's actual languages/frameworks, package manager, entry points, existing test/build/lint/format/type-check commands, and conventions — before assuming any of them. |
| `requirements-analysis` | `feature`, `requirement`, `bug`, `unclear`, `ambiguous`, `vague` | Pin down the requested outcome, acceptance criteria, constraints/assumptions, edge cases, and what "verified" means for this task. |
| `implementation-planning` | `refactor`, `architecture`, `redesign`, `restructure`, `rearchitect` | Inspect existing code first, find the controlling code path, plan the smallest coherent change, avoid unrelated refactors, revise the plan when evidence changes. |
| `testing-and-verification` | `test`, `implement`, `fix`, `bug` | Discover the project's own verification commands, add focused tests, run test/build/lint/type-check when configured, read real output, never weaken a test to pass it, distinguish "no tests found" from "verified." |
| `debugging-and-failure-repair` | `error`, `fail`, `failing`, `failed`, `traceback`, `exception`, `crash`, `broken` | Use the real failure output, find the root cause, make a minimal targeted fix, rerun the failing check, avoid blind retries and broad rewrites. |
| `security-review` | `auth`, `authentication`, `authorization`, `login`, `password`, `token`, `secret`, `credential`, `session`, `payment`, `api`, `database`, `deploy`, `production` | Secret exposure, injection risks, authentication/authorization, unsafe deserialization, sensitive data leakage, insecure defaults, dependency/config risks. |
| `documentation-and-operational-readiness` | `api`, `config`, `configuration`, `setup`, `install`, `deploy`, `cli`, `endpoint` | Keep README/docs, install/run instructions, the configuration reference, API examples, and migration notes in sync with what actually changed. |
| `completion-and-release-readiness` | `release`, `deploy`, `production`, `ship`, `launch`, `pull request`, `PR` | Final pre-finish review across acceptance criteria, changed behavior, verification results, docs, and security — explicitly prohibits claiming success while required verification is failing or unavailable. |

**These supersede an earlier five-skill set** (`requirements-analysis`,
`implementation-planning`, `testing-and-verification`, `security-review`,
`release-readiness`) that lived as `SKILL.md`-format, `invoke_skill`-based
skills — downloaded from `addyosmani/agent-skills` per an earlier request.
That set is gone; see ROADMAP.md's decisions log for why (short version:
these need real, code-enforced triggers and short, harness-aware content —
concerns generic downloaded skills, written for a different project's own
conventions, couldn't satisfy). `testing-and-verification` and
`security-review` keep their old names (content fully rewritten);
`release-readiness` is renamed `completion-and-release-readiness`;
`repository-discovery` and `documentation-and-operational-readiness` are new.

None of these duplicate `agent.py`'s own always-on policies (autonomy,
non-interactive tooling, the README requirement, verify-before-finish) —
each covers lifecycle ground those policies don't. `agent.py`'s
`_LIFECYCLE_SKILLS_SUFFIX` tells the agent to inspect the repository before
editing, to apply a lifecycle skill's guidance even when its trigger word
doesn't happen to appear in the task text, to skip a skill outright for
trivial changes, and — explicitly — that invoking or reading a skill is
never a substitute for actually running the checks it describes.

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

### Per-request override (no `.env` edit)

To try the *same* task against a different model/provider, or the same
model at a different reasoning-effort level, without touching `.env` — e.g.
comparing how two models (or two effort levels) handle one task — every
entry point accepts an explicit, optional override instead:

- CLI: `--model` / `--api-key` / `--base-url` / `--reasoning-effort` (see
  [CLI reference](#cli-reference)).
- `POST /tasks` and `WS /tasks/stream`: `model` / `api_key` / `base_url` /
  `reasoning_effort` fields.
- `POST /v1/chat/completions`: `llm_model` / `llm_api_key` / `llm_base_url` /
  `llm_reasoning_effort` (kept separate from the wire-mandated `model` field
  — see
  [`POST /v1/chat/completions`](#post-v1chatcompletions--openai-compatible-adapter)).

`LLM_REASONING_EFFORT`/`reasoning_effort` is provider-neutral — passed
straight through to the SDK's own `LLM.reasoning_effort` field (LiteLLM
translates it per-provider). Leave it unset to use the SDK's own default
(`"high"`).

Any subset may be given; an omitted field keeps `.env`'s value for that one
setting (e.g. passing only `--model` keeps the configured `LLM_API_KEY`). This
is `harness.config.override_llm()` under the hood — it returns a modified
copy of the resolved `Config`, `.env` itself is never touched, so the override
applies to that one run only. This is additive to, not a replacement for, the
`.env`-only invariant above: with no override given, behavior is unchanged.

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
- `custom_tools/run_tests_tool.py` — real tool (`run_tests`): verifies the
  project actually works, instead of making the agent shell out through the
  terminal tool and hand-parse raw output, for whatever language the
  project is written in. Internally this is a language-neutral pipeline in
  four explicit stages, each independently unit-tested
  (`tests/custom_tools/test_run_tests_tool.py`):

  1. **Project detection** (`detect_project()`) — a single, depth-bounded
     tree walk (a scaffolded project can land nested a level or two below
     the workspace root — confirmed live) checking every known ecosystem's
     manifest marker together: `pyproject.toml`/`setup.py`/`setup.cfg`
     (Python), `package.json` (Node), `go.mod` (Go), `Cargo.toml` (Rust),
     `pom.xml` (Maven) or `build.gradle`/`build.gradle.kts` (Gradle). No
     manifest but real `.py` source present still resolves to Python (the
     original always-try-pytest default, preserved for a bare directory of
     test files); no marker and no Python source at all resolves to
     `"unknown"`.
  2. **Verification-plan discovery** (`discover_verification_plan()`) — pure
     (file reads + `shutil.which` lookups, no subprocess execution): given a
     detected language, decides which commands apply. Never invents a
     command a project doesn't itself configure or that can't be safely
     inferred — an unknown project gets a single explicit "verification
     unavailable" entry instead of a guess.
  3. **Verification command execution** (`execute_check()`) — the only stage
     that spawns a subprocess; turns one planned check into a structured
     result.
  4. **Structured verification results** (`CheckOutcome` / `VerificationRun`)
     — every check outcome is one of exactly five statuses: `passed`,
     `failed`, `skipped` (not applicable/configured here, or ran but found
     nothing to verify), `unavailable` (should be checkable but isn't — a
     missing tool, or an unknown project type), `timed_out`. Each carries
     its command, exit code, project root, and bounded (last ~4000 chars)
     output.

  The agent-facing `run_tests` tool always runs exactly one check (its
  contract, unchanged since Milestone 4) — for Python/Node it keeps its
  original rich behavior (pytest's pass/fail/error counts and `path`
  targeting; `npm run build`'s exit status); for Go/Rust/Java it reuses
  stages 1–3 above and reports the primary check's generic outcome. See
  [Known limitations](#known-limitations) for exactly what's verified vs.
  only detected per language.

  `run_full_verification()` — harness-side only, never called by the agent
  — runs the *full* plan: the primary check plus whatever secondary checks
  the project configures. Per language (never invented — a check only runs
  if the project itself configures it):

  | Language | Primary check | Secondary checks (only if configured) |
  |---|---|---|
  | Python | `pytest -q` | `ruff check .` (`[tool.ruff]` or `ruff.toml`/`.ruff.toml`); `mypy .` (`[tool.mypy]` or `mypy.ini`/`.mypy.ini`); **always**, if any `.py` file at the project root has an `if __name__ == "__main__":` guard: `entrypoint-ordering` (a static AST check, no subprocess — see below) and `python <entry point>` (actually runs it, stdin closed) |
  | Node/JS/TS | `npm run build` if defined, else `npm test` if a real (non-placeholder) `scripts.test` exists | whichever of `scripts.test`/`scripts.lint`/`scripts.typecheck`(`-type-check`) aren't already primary |
  | Go | `go test ./...` | `go vet ./...` |
  | Rust | `cargo test` | `cargo check`; `cargo clippy` |
  | Java (Maven) | `mvn test` (prefers a checked-in `./mvnw` wrapper) | — |
  | Java (Gradle) | `gradle test` (prefers a checked-in `./gradlew` wrapper) | — |

  **The two Python entry-point checks exist because a passing pytest suite
  only proves the code works when *imported* — confirmed live it can
  silently miss a real crash.** A generated `hanoi.py` defined a `solve()`
  function *after* its own `if __name__ == "__main__": main()` block, and
  `main()` called `solve()` only when a user chose the `SOLVE` menu option.
  `test_hanoi.py` did `from hanoi import HanoiGame, solve` and called
  `solve()` directly — importing the module runs the *whole file*
  top-to-bottom (`__name__ != "__main__"` on import, so the guarded
  `main()` call never fires), so `solve` is fully defined by the time any
  test uses it, and every test passed. Running `python hanoi.py` directly
  calls into the guard immediately, before the interpreter ever reaches the
  later `def solve(...)` — `NameError: name 'solve' is not defined` the
  moment a user picked `SOLVE`, on a project the harness had just reported
  `Verification: verified` for.
  - `entrypoint-ordering` is a pure static check (`ast`-parses each
    root-level entry-point file, no subprocess): fails if anything is
    defined *after* the file's own `__main__` guard, which is exactly what
    happened here — this is the check that catches this specific bug class
    precisely, by name.
  - `python <entry point>` actually runs the script with **stdin closed**
    (no human is available to answer a prompt — see [Known
    limitations](#known-limitations)), so it can only prove the script
    doesn't crash before or without reading any input; it cannot exercise
    an interactive menu path like `SOLVE` itself. A program that doesn't
    handle closed/absent stdin gracefully (an uncaught `EOFError` on the
    first prompt) will show up here as a failure too — a real, if narrower,
    finding, not a false positive.

  A configured tool that isn't installed (ruff/mypy/npm/go/cargo/mvn/gradle
  missing) is reported as a limitation, not a failure. See [Test
  verification](#test-verification) for how this feeds the post-run retry
  loop.

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

## Test verification

The agent's own "finish" message is not proof a task actually succeeded —
confirmed live twice, in two different ways:

1. Asked to build a Tower of Hanoi game, the agent called `finish` saying
   tests needed "further debugging," but the real bug was an
   `IndentationError` in the file it wrote (it didn't even parse), which it
   had never actually re-run to check.
2. Asked to scaffold a Vite/React booking site, the agent called `finish`
   declaring the site "set up successfully" while `App.tsx` had a JSX parse
   error (adjacent JSX elements not wrapped in an enclosing tag/fragment) —
   the dev server it claimed was "running" would fail to render at all.
   Pytest-only verification didn't catch this either: run against that
   project, pytest reports "no tests collected" (exit code `5`), which was
   (rightly, for a Python project) treated as "nothing to verify," but was a
   false pass here since the project isn't Python at all.
3. Asked to create two `task_tracker` items and leave one `todo`, the agent
   did exactly that and then called `finish` anyway — the same "declares
   done, self-report doesn't match reality" pattern as the two above, just
   surfaced through the built-in task-tracking tool instead of the project's
   own tests.

`HARNESS_VERIFY_TESTS=always` (the default) is the harness's own safety net
against all three: after every run, `runner.py` first checks the agent's own
`task_tracker` list (see "Task-tracker completion" below), then re-runs the
project's own verification itself — via `run_tests_tool.run_full_verification()`,
no LLM call involved (see [Custom tools](#custom-tools) for exactly what that
checks, in any of Python/Node/Go/Rust/Java). The result is always one of
nine states, exposed as `verification_state` everywhere a result reaches a
caller (CLI stdout, `GET /tasks/{id}`, the `WS /tasks/stream` `"result"`
event — see [CLI reference](#cli-reference) / [Server mode](#server-mode-httpwebsocket)):

| State | Meaning |
|---|---|
| `verified` | The project's primary check actually ran and passed. A configured secondary check (lint/typecheck/`go vet`/`cargo check`/`cargo clippy`/etc.) that couldn't run because its tool isn't installed doesn't block this — it's recorded as a limitation instead, not a failure. |
| `inconclusive` | Nothing runnable confirmed the software works — either the specific "pytest collected zero tests" signal (exit code `5`; kept silent/non-blocking, same as before — a project can legitimately have no tests yet), or a genuine "couldn't check anything" case (a required tool isn't installed, or the project type itself is unknown — see the table in [Custom tools](#custom-tools)), which **is** surfaced with a visible harness notice so it isn't mistaken for a confirmed pass. Never returned as `verified` — see the point above about not treating "no tests found" as proof of correctness. |
| `retry_exhausted` | A real failure (primary or any configured secondary check) was found and sent back to the agent to fix, but it was still failing — with the failure actually changing between attempts (see `no_progress` below for when it doesn't) — after `HARNESS_MAX_VERIFY_RETRIES` attempts. The harness appends its own message saying so plainly, rather than letting the agent's last (possibly optimistic) message stand as the final word. |
| `no_progress` | A fix attempt was sent back to the agent, but the *very next* verification pass came back with the exact same failing check, same exit code, and the same output (only a run-duration footer, like pytest's `in 3.85s`, is allowed to differ) — meaning that specific attempt provably changed nothing observable. Stops immediately, before exhausting the rest of the retry budget, rather than spending it on further attempts already shown not to help. Confirmed live: an agent edited a comparison operator to "fix" a failing test twice in a row while its own explanatory messages degraded into fluent-sounding but empty prose (see ROADMAP.md's decisions log) — both edits were no-ops for that specific failure (a different code branch handled it), and pytest's output was identical before and after. This is a cheaper, more reliable signal than trying to judge whether the agent's own reasoning text still makes sense — it doesn't read the agent's prose at all, only the verification output. |
| `timed_out` | A check exceeded its timeout (300s) and was killed — retried the same as a real failure (usually an infinite loop or a hang the agent introduced, worth one more attempt to fix), but kept as its own terminal state rather than folded into `retry_exhausted` if it's still timing out after the retry budget: a persistent hang is a different problem from a wrong answer, worth telling apart at a glance. (Two identical timeouts in a row are `no_progress`, not this — same rule as any other check.) |
| `incomplete` | The agent's own `task_tracker` list still had an item marked `todo`/`in_progress` after `HARNESS_MAX_VERIFY_RETRIES` automated follow-ups — see "Task-tracker completion" below. Project verification is skipped entirely in this case: a task the agent's own tracking says isn't finished can't be meaningfully "verified" by running its tests. |
| `confirmation_required` | `HARNESS_CONFIRM_MODE=always` paused before a tool call, but no interactive approval handler was available to answer it (e.g. server mode, or any caller that didn't supply one) — see [Confirmation mode](#confirmation-mode) below. The harness rejects the pending action once and stops rather than silently approving it or waiting indefinitely; task_tracker and project verification are both skipped, since the run never actually continued. |
| `budget_exhausted` | The task's shared, wall-clock `HARNESS_MAX_TASK_SECONDS` budget ran out — spanning the initial run and every task_tracker/verification retry combined, not just one `conversation.run()` call. See [Task budget](#task-budget) below. |
| `stuck` | The conversation's own `execution_status` (the SDK's stuck-loop/error detection) ended in `stuck` or `error` rather than a normal finish — verification isn't even attempted against a run that never reached a coherent stopping point. Checked before the initial verification pass and again after every retry, and before/during the task-tracker, confirmation, and budget checks too. |

(A tenth value, `failed`, exists only as the momentary signal inside the
retry loop between "a check just failed" and "was it fixed, or did retries
run out" — it never appears as a run's final `verification_state`.)

**Task-tracker completion.** Independently of test verification,
`runner.py` also checks the agent's own `task_tracker` tool state (one of
the SDK's default tools, always registered by `get_default_tools()`) right
after the agent's first run: it looks at the most recently observed task
list and,
if any item is still `todo`/`in_progress`, sends the agent a follow-up
listing exactly which ones and re-runs it, bounded by the same
`HARNESS_MAX_VERIFY_RETRIES` budget used for test-fix retries. If the agent
never used `task_tracker` at all, or its list is already fully `done`,
nothing happens — the tool's own guidance says trivial tasks don't need it,
so an unused tracker is not treated as incomplete work. This runs *before*
project verification, so a task left genuinely unfinished (per the agent's
own tracking) is reported as `incomplete` without ever reaching — and
potentially "passing" — a test suite that only covers the part that did get
built.

**`no_progress` is deliberately not based on reading the agent's own
message text.** The SDK's own stuck-loop detector (`stuck_detection=True`
by default) only fires on *exact* repetition of actions/observations/
messages — confirmed by reading `openhands/sdk/conversation/
stuck_detector.py` directly — so it does not catch a case where the agent
takes a genuinely different action (a different edit) and writes genuinely
different (if increasingly incoherent) text each time, which is exactly
what was observed live. A text-coherence classifier was considered and
rejected in favor of comparing the actual, structured verification output
instead — see ROADMAP.md's decisions log for the reasoning.

Every result also carries a `completion_contract`
(`{"goal", "acceptance_criteria", "verification_checks", "limitations"}`) —
a harness-computed record of what the task was actually judged against, not
another prompt fed back to the model: `verification_checks` lists the exact
commands run (e.g. `["pytest -q", "ruff check .", "go test ./..."]`), and
`limitations` lists anything that couldn't be confirmed (an unavailable
secondary check, a stuck run, `HARNESS_VERIFY_TESTS=never`, etc.).

Set `HARNESS_VERIFY_TESTS=never` to skip verification entirely — e.g. while
iterating quickly and you'd rather review failures yourself. The result is
always `inconclusive` in that case (with a limitation noting verification
was skipped), never `verified` — skipping the check is not the same as
confirming the work.

## Confirmation mode

`HARNESS_CONFIRM_MODE=always` pauses the agent before every tool call and
requires explicit approval before it runs — the SDK's `AlwaysConfirm`
confirmation policy, attached in `runner.py`. In the CLI, this means an
interactive prompt at the terminal:

```
--- Confirmation required (HARNESS_CONFIRM_MODE=always) ---
  terminal: command='rm -rf build/' is_input=False timeout=None reset=False kind='TerminalAction'
Approve? [y/N]
```

Anything other than an explicit `y`/`yes` rejects the action — the agent
sees the rejection and can try something else or explain why it's stuck,
the same as if a human had said "no, don't do that." Approving lets exactly
that one action run; the agent is paused again before its next tool call.

**Server mode has no way to answer this prompt** (there's no terminal to
read from), so if `HARNESS_CONFIRM_MODE=always` is set and the agent
proposes any tool call, the harness rejects that one action automatically
and stops the task immediately with `verification_state:
"confirmation_required"` — it does not silently approve the action (that
would defeat the whole point of confirm mode) and does not hang waiting for
an answer nobody can give. In practice, `HARNESS_CONFIRM_MODE=always` is a
CLI-only feature today; leave it at the default `never` for server-mode
deployments.

`--confirm-mode` is not a CLI flag — this is purely a `.env`/`HARNESS_CONFIRM_MODE`
setting, consistent with `confirm_mode` not being part of the per-request
LLM override set (`--model`/`--api-key`/`--base-url`/`--reasoning-effort`).

## Task budget

`HARNESS_MAX_ITERATIONS` bounds a single `conversation.run()` call, but
`runner.py` can call `.run()` up to three times for one task: the initial
run, then up to `HARNESS_MAX_VERIFY_RETRIES` more inside
`_enforce_task_tracker_completion`'s retry loop, then up to
`HARNESS_MAX_VERIFY_RETRIES` more again inside `_verify_and_report`'s retry
loop — and each call gets its own fresh iteration budget from the SDK, not
a shared one. Worst case, a single task could spend roughly
`HARNESS_MAX_ITERATIONS × (1 + 2 × HARNESS_MAX_VERIFY_RETRIES)` iterations,
well beyond what the configured cap on its own suggests.

`HARNESS_MAX_TASK_SECONDS` (default `1800`, i.e. 30 minutes) closes that
gap with a shared, task-level wall-clock budget spanning every phase
combined — not a replacement for `HARNESS_MAX_ITERATIONS` (kept as a
secondary, per-call guard), a complementary one. It's checked before every
single `conversation.run()` call the harness makes for a task, including
each confirm-mode approve/reject round-trip — not just once at the start —
so a long back-and-forth can't exceed it either. When it runs out, the
task stops immediately with `verification_state: "budget_exhausted"`,
regardless of which phase it was in; task_tracker and project verification
are both skipped if the budget ran out before reaching them, the same way
`"confirmation_required"` short-circuits them.

This is a wall-clock budget, not a hard interrupt mid-`conversation.run()`
call — a check only happens *between* calls, the same checkpoint-based
model `HARNESS_MAX_VERIFY_RETRIES` already uses. A single very slow
iteration (a huge context, a slow LLM response) can still push the actual
elapsed time somewhat past the configured value before the next checkpoint
catches it; this bounds runaway *retry multiplication*, not sub-second
precision timing.

## Acceptance checks

`CompletionContract.acceptance_criteria` records what a task was expected
to accomplish, but by itself it's never independently checked against
anything — it's descriptive text, not a test. `--acceptance-checks` (CLI)
/ `acceptance_checks` (`POST /tasks`, `WS /tasks/stream`) is an **optional,
opt-in** way to give the harness something concrete to check for *this
specific task*, on top of (not instead of) the project's own tests/build.

A caller supplies a JSON array of check objects:

```json
[
  {"kind": "file_exists", "path": "README.md"},
  {
    "kind": "file_contains",
    "path": "README.md",
    "contains": "## Usage",
    "required": false,
    "description": "README documents usage"
  }
]
```

- `kind` — `"file_exists"` or `"file_contains"` only. There is deliberately
  no "run this command" kind, even though that was originally considered —
  see [Known limitations](#known-limitations) for why.
- `path` — always resolved relative to the task's own workspace, and
  cannot escape it: an absolute path or a `..` segment is rejected
  outright, and the resolved, symlink-followed location is checked against
  the workspace the same way `resolve_project_dir()` protects
  `HARNESS_PROJECTS_DIR` (see [Projects](#projects-one-subfolder-per-generated-project)).
  Without this, an unauthenticated caller (server mode has no
  authentication) could otherwise ask "does `/etc/passwd` contain `root`"
  and learn about arbitrary host files.
- `contains` — required for `"file_contains"`, ignored (and rejected) for
  `"file_exists"`.
- `required` — defaults to `true`. A failing **required** check downgrades
  an otherwise-`"verified"` result to a new terminal state,
  `"acceptance_failed"` — every other `verification_state` is left exactly
  as it was, since a run that already failed some other way has nothing
  left to "block". A failing **optional** check is still recorded (in
  `limitations` and in the structured `acceptance_results`) but never
  changes `verification_state`.
- `description` — optional, human-readable label used in reports instead
  of the raw `kind`/`path`.

CLI: `--acceptance-checks` accepts either a path to a local JSON file or
the JSON inline (same "existing file wins over literal text" resolution as
the `task` argument itself, minus the URL-fetch case). Server mode always
takes inline JSON (a request body field, not a file path).

Every check is evaluated (a file read capped at 1MB for `file_contains`)
regardless of the underlying verification outcome, and its result is
attached to `TaskOutcome.acceptance_results` — exposed as
`acceptance_results` in `GET /tasks/{task_id}` and the WS `"result"`
frame — even when nothing was downgraded, so a caller can always see
exactly what was checked and what was found.

## Testing

```bash
uv run pytest -q
```

- `tests/test_config.py` — env parsing, no SDK, no network. Includes
  `resolve_project_dir()`'s containment checks: a normal name resolves
  inside `projects_dir`, an absolute name/`..`-traversal/empty-or-dot name
  is rejected, and a real symlink (created on disk with `tmp_path`, not
  simulated) that resolves outside `projects_dir` is caught while one that
  resolves back inside it is allowed. Also covers `HARNESS_MAX_TASK_SECONDS`
  parsing (default, a custom value, and rejecting non-positive/non-integer
  values).
- `tests/test_acceptance.py` — pure filesystem checks, no SDK, no network.
  Input validation (`parse_acceptance_check`: unknown kind, missing/
  invalid `path`, `file_contains` requiring `contains`, non-bool
  `required`, non-object items), `file_exists`/`file_contains` evaluation
  against real files in `tmp_path` (including a real capped-read
  truncation case, not simulated), and the same path-containment
  protection as `resolve_project_dir` — an absolute path, `..` traversal,
  and a real on-disk symlink escape are all rejected, while a symlink that
  stays inside the workspace is allowed.
- `tests/test_skills.py` — `load_skill_catalog()` (against a temp directory
  with nested subfolders) and `write_project_context()`, no LLM.
- `tests/custom_tools/test_*.py` — tool executors called directly, no LLM.
  `test_run_tests_tool.py` covers the full detect → discover-plan → execute
  → aggregate pipeline stage by stage (project detection per language,
  plan discovery's command resolution, `execute_check()`'s five statuses
  including a monkeypatched `subprocess.TimeoutExpired`, `execute_check`
  returning a `precomputed` outcome untouched with no subprocess spawned,
  and `VerificationRun.state`'s aggregation rules including a mixed
  one-passes-one-fails case) plus `run_full_verification()` end to end
  against real subprocesses wherever the toolchain is installed in this dev
  environment (`pytest`, `npm`, `ruff`, `go`, `cargo` — each gated by its
  own skip-if-missing marker) and the real "configured but not installed"
  path for `mypy` and Maven (neither is installed here, so those tests
  exercise the actual gap, not a simulated one). Also covers the two Python
  entry-point checks directly — `_find_python_entrypoints`/
  `_entrypoint_ordering_spec` against synthetic fixtures reproducing the
  exact live ordering bug (and its fixed form), `run_full_verification()`
  showing that bug alongside a passing pytest suite still reports `failed`
  overall, and a regression test confirming `execute_check` closes stdin so
  a script that reads it fails fast instead of hanging the whole check.
- `tests/test_workspace.py` — `build_workspace()` dispatch; the Docker branch
  monkeypatches `subprocess` and `DockerWorkspace`, so this suite never
  touches a real Docker daemon. Skips cleanly
  (`pytest.importorskip("openhands.workspace")`) when the `sandbox` extra
  isn't installed.
- `tests/test_cli.py` — argument parsing and control flow; `load_config`/
  `run_task` are monkeypatched, no LLM, no Docker. Includes
  `resolve_task_source()` (literal/file/URL, with `urlopen` monkeypatched —
  no real network call), and `_confirm_pending_actions` (only an explicit
  `y`/`yes` approves; `builtins.input` monkeypatched, no real terminal) plus
  confirming `cli.main` only passes it to `run_task` when
  `confirm_mode == "always"`, a regression test confirming `--project
  ../escaped` is rejected with a configuration error rather than creating
  a directory outside `HARNESS_PROJECTS_DIR`, `--require-verification`
  (default off, an unknown project type/missing tool/no-tests-collected
  case all become exit `1` when passed, other verification states and the
  default-off case are unaffected), a regression test confirming
  `budget_exhausted` is a nonzero exit (verified live: reverting the fix
  makes this test fail with `assert 0 == 1`), and `resolve_acceptance_checks`/
  `--acceptance-checks` (inline JSON, reading a file, rejecting invalid
  JSON/a non-array/an invalid check kind, `acceptance_failed` being a
  nonzero exit).
- `tests/test_server.py` — REST/WebSocket/OpenAI-compatible routes via
  FastAPI's `TestClient` (SSE streaming read via `client.stream(...)` +
  `iter_lines()`); `load_config`/`run_task`/`stream_task` are monkeypatched,
  no LLM. Includes a regression test for the `role == "assistant"` filtering
  bug (see MANUAL.md "OpenAI-compatible adapter"), one confirming
  `POST /tasks`'s `project` field rejects an absolute path with a `400`
  instead of resolving it, and `require_verification` coverage for both
  `POST /tasks` (default `status: "completed"` for `inconclusive`, flips to
  `"failed"` with an explanatory `error` when set, unknown project type/
  missing tool/no-tests-collected all covered, other verification states
  unaffected) and `WS /tasks/stream` (sends a `"type": "error"` frame
  instead of `"result"` in the same situation). Also covers
  `acceptance_checks`: an invalid check returns `400` before a task is
  created, a parsed list is passed through to `stream_task`, `None` when
  omitted, and `GET /tasks/{task_id}`/the WS `"result"` frame both expose
  `acceptance_results` (serialized via `asdict`, confirmed against a real
  `AcceptanceCheckResult`/`AcceptanceCheck` pair, not a hand-built dict).
  Skips cleanly (`pytest.importorskip("fastapi")`) when the `server` extra
  isn't installed.
- `tests/test_runner.py` — one real end-to-end smoke test (**skips cleanly**
  when `LLM_MODEL`/`LLM_API_KEY` aren't configured; when they are, it makes
  one real, cheap LLM call, asserts a file was actually created, and asserts
  `.outcome.verification_state` is a valid state — that's intentional, spec
  section 10, not a leak), plus the harness-side verification-loop unit
  tests (`_verify_and_report`, no LLM): successful verification, a failing
  check followed by a successful retry, retry exhaustion (with the failure
  output genuinely differing between attempts, distinguishing it from
  `no_progress`), a fix attempt that changes nothing observable — including
  one case verified against the actual on-disk repro that motivated it (see
  ROADMAP.md's decisions log) and one confirming timing-only differences
  (e.g. pytest's `in 3.85s` footer) don't count as real change — a
  timed-out check that recovers on retry and one that keeps timing out
  through the retry budget (its own terminal `timed_out` state, distinct
  from `retry_exhausted`), both flavors of inconclusive (the silent "pytest
  collected zero tests" case and the visibly-flagged "nothing runnable"
  case), and the agent stuck/error-before-finish path (a fake `Conversation`
  whose `state.execution_status` reports `ConversationExecutionStatus.STUCK`/
  `ERROR`, confirmed against the real SDK enum — see ROADMAP.md's decisions
  log). Also covers `_enforce_task_tracker_completion` (no LLM): unused/
  fully-done trackers aren't enforced, a pending item triggers a follow-up
  that recovers on retry, retries exhausting with items still pending
  reports `incomplete`, the stuck/error path before and during a retry, and
  one `stream_task`-level integration test confirming an incomplete tracker
  short-circuits project verification entirely (it's never even attempted)
  — using real `ObservationEvent`/`TaskTrackerObservation` instances, not
  bare mocks, so the `isinstance` check `_task_tracker_snapshot` relies on
  is actually exercised. Also covers `_run_with_confirmation` (no LLM):
  a no-op when `confirm_mode != "always"`, approving and rejecting a
  pending action and continuing either way, stopping and rejecting exactly
  once when no `on_confirm` handler is available, `stream_task` actually
  attaching `AlwaysConfirm` when `confirm_mode == "always"`, and a
  `stream_task`-level integration test confirming an unanswered
  confirmation short-circuits task_tracker/project verification entirely.
  Confirmed live (both the approve and reject paths) via the real CLI
  against an isolated workspace, piping `y`/`n` into
  `_confirm_pending_actions`'s `input()` prompt. `HARNESS_MAX_TASK_SECONDS`
  coverage: `_run_with_confirmation` reports `budget_exhausted` before its
  first `.run()` call when the deadline has already passed, and again mid
  confirm-loop (a mocked, increasing `time.monotonic()` sequence simulates
  time passing between checks); `_verify_and_report` and
  `_enforce_task_tracker_completion` each report it both at entry and
  mid-retry; and a `stream_task`-level integration test confirms an
  already-exhausted budget short-circuits task_tracker/project verification
  entirely. Confirmed live via the real CLI (`HARNESS_MAX_TASK_SECONDS=1`
  against a trivial task) that the task still completes its work but is
  correctly reported as `budget_exhausted` with exit code `1`, and that a
  normal task under the default budget is unaffected. `acceptance_checks`
  coverage: `_apply_acceptance_checks` is a no-op with none supplied, all
  checks passing keeps `"verified"`, a failing required check downgrades
  `"verified"` to `"acceptance_failed"` (with a harness notice emitted), a
  failing optional check is recorded but never downgrades, a non-`"verified"`
  starting state is left alone (nothing left to "block"), and a rejected
  path (e.g. absolute) fails cleanly as a result rather than raising —
  plus two `stream_task`-level integration tests against a real filesystem
  confirming a passing check keeps `"verified"` and a failing required one
  downgrades a real `stream_task` run end to end.

## Known limitations

- **Acceptance checks support only `file_exists`/`file_contains` — no
  "run this command" kind, even though that was one of the kinds
  originally proposed.** A caller-supplied command check would be a new,
  harness-triggered remote-code-execution surface: the harness itself
  would run whatever the caller specified, not the agent inside its own
  tool loop — and that compounds directly with server mode having no
  authentication (see the `POST /tasks`/`WS /tasks/stream` entry below):
  an unauthenticated network caller could otherwise get arbitrary command
  execution on the host with no agent, no confirm-mode gate, and no review
  involved at all. A caller-supplied file *path* check doesn't have this
  problem once properly contained to the task's own workspace (see
  [Acceptance checks](#acceptance-checks)), so that's what's implemented;
  a command kind is deliberately out of scope until server-mode
  authentication exists, at minimum.
- **`file_editor` requires absolute paths.** It does not resolve a relative
  path like `"HELLO.txt"` against the workspace directory — the model has to
  supply (or discover, e.g. via the terminal tool) a full absolute path.
  `--project` mitigates this by always resolving the workspace to an absolute
  path, but the task text or the model's own exploration still needs to
  arrive at the right absolute file path.
- **Verification covers six project kinds (Python, Node, Go, Rust, Java
  Maven, Java Gradle) via unambiguous manifest markers, not every
  ecosystem or build tool.** Detection (`detect_project()`) is a single
  depth-bounded tree walk over the markers in the table in [Custom
  tools](#custom-tools); a project with no matching marker and no Python
  source at all is `"unknown"` — an explicit `unavailable` result, never a
  guessed command (see [Test verification](#test-verification)). What's
  genuinely *verified* vs. only *detected* differs by language:
  - **Python, Node**: fully verified — real commands actually run
    (`pytest`/`ruff`/`mypy`; `npm run build`/`test`/`lint`/`typecheck`).
  - **Go, Rust**: verified whenever the toolchain (`go`/`cargo`) is on
    `PATH` — real, unit-tested subprocess runs (`go test ./...`, `go vet
    ./...`, `cargo test`, `cargo check`, `cargo clippy`); reported
    `unavailable` otherwise.
  - **Java (Maven/Gradle)**: *detected* reliably (manifest markers, wrapper
    vs. global-tool resolution), but *verification itself* depends entirely
    on `mvn`/`./mvnw` or `gradle`/`./gradlew` being present — neither ships
    in this project's own dev/CI environment or the Docker agent-server
    image, so in practice a Java project reports `unavailable` unless the
    workspace (or a custom Docker image) provides one of those.
  It does **not** run a JS project's own test runner's internal
  config/reporting beyond invoking its defined `scripts.test` as an opaque
  command (no jest/vitest-specific parsing) and does not cover any other
  ecosystem (Ruby, PHP, .NET, C/C++, ...) or Python lint/typecheck tool
  beyond ruff/mypy specifically (flake8, pylint, pyright are not detected).
  Under `docker` execution, none of these toolchains are guaranteed to be
  installed in the image, and a `--project` workspace holds newly generated
  software, not this repo — so verification may not be meaningful there
  depending on what's actually in the image (a missing tool is reported as
  a limitation, not a false failure). Making this fully generic (detect any
  project's actual test runner, install its dependencies) is unscoped. The
  `HARNESS_VERIFY_TESTS` safety net runs against the local, bind-mounted
  project directory using the harness's own installed toolchains (this
  works even under `docker` execution, since `workspace.py` bind-mounts the
  project directory onto the host either way) — a project of an unknown
  kind, or one whose toolchain isn't installed, just reports nothing found
  and is left `inconclusive`, not `verified` and not `failed`.
- **The Python entry-point smoke test (`python <entry point>`, stdin
  closed) cannot exercise interactive menu paths.** It proves a script
  doesn't crash on startup with no input available — it cannot prove that
  choosing a specific command (a `SOLVE`/`HELP`/`RESET`-style menu option)
  works, since reaching that code path requires typing something the
  script would actually read, and there's no general, safe way to guess
  what a given interactive program expects. The companion
  `entrypoint-ordering` static check (see [Custom tools](#custom-tools))
  narrows this gap for one specific, common bug class — a name referenced
  from inside the `__main__` guard that isn't defined until later in the
  file — by checking the file's structure directly instead of trying to
  drive the program into every branch; a bug reachable only through a
  *correctly-ordered* but otherwise-broken interactive branch is still
  outside what either check can catch.
- **`local` execution's terminal has no real PTY unless `tmux` is installed
  on the host** — without it, the SDK falls back to a subprocess-based
  terminal (a startup warning says so). `docker` execution is unaffected:
  `tmux` ships in the published `ghcr.io/openhands/agent-server` base image
  already (confirmed live: `tmux -V` → `3.5a`, works as the container's
  non-root `openhands` user), so no Dockerfile change was needed there.
  Regardless of PTY support, an interactive CLI prompt (an `npm
  create`/scaffolding wizard, a package manager's "confirm install?" prompt)
  has no human to answer it, so it will still auto-cancel — often with exit
  code `0` and nothing actually created — unless the agent avoids it in the
  first place. The agent is instructed (`agent.py`'s
  `_NONINTERACTIVE_TOOLING_SUFFIX`) to use each tool's non-interactive/CI
  flags and to verify a command's actual result instead of trusting its exit
  code, but this is a soft prompt-level instruction, not a hard guarantee.
  Install `tmux` locally with `brew install tmux` (macOS) / `apt-get install
  tmux` (Linux) for terminal stability generally.
- **Only `local` and `docker` execution exist.** ECS/EC2 (mentioned as a
  future direction) are not implemented; `workspace.py` is structured so
  adding one is additive, not a rewrite.
- **Provider-swap live proof (spec Milestone 3) hasn't been run** — see
  [Switching LLM provider / model](#switching-llm-provider--model).
- **Server mode's task registry is in-memory, per-process, and unbounded.**
  `POST`/`GET /tasks` state is lost on restart, not shared across multiple
  server processes, and completed/failed records are never purged — see
  [Server mode](#server-mode-httpwebsocket).
- **The OpenAI-compatible endpoint doesn't replay chat history** — only the
  last `user` message becomes the task; prior `assistant` turns are dropped.
  **Streaming is message-level, not token-level** — each SSE chunk is one
  full agent message, not an incremental token (the underlying agent loop's
  event callback doesn't expose token-level granularity). **`usage` is
  always zeroed** — token counts aren't tracked across a whole agent loop.
  **No authentication on any server-mode endpoint**, including this one —
  an OpenAI client sending an `Authorization` header has it silently
  ignored, not validated.

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
