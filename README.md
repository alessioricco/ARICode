```
   _____ __________.___  _________            .___      
  /  _  \\______   \   | \_   ___ \  ____   __| _/____  
 /  /_\  \|       _/   | /    \  \/ /  _ \ / __ |/ __ \ 
/    |    \    |   \   | \     \___(  <_> ) /_/ \  ___/ 
\____|__  /____|_  /___|  \______  /\____/\____ |\___  >
        \/       \/              \/            \/    \/ 
```

# ARICode — Agentic Routing Intelligence for autonomous software engineering agents.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Built on OpenHands SDK](https://img.shields.io/badge/built%20on-OpenHands%20SDK-6f42c1.svg)

Give ARICode a task. It plans, edits files, runs commands, and iterates in a
loop using the [OpenHands Software Agent SDK](https://docs.openhands.dev/sdk)
— but unlike a bare agent loop, it doesn't take the agent's word for it:
ARICode independently re-runs the project's own tests/build after the agent
finishes and only reports success once that's actually confirmed. Switch LLM
provider or model with a one-line `.env` edit; no code change, ever.

**See [`MANUAL.md`](MANUAL.md) for the full usage guide** — configuration
reference, CLI flags, projects, execution modes, server mode, adding a
custom tool, and known limitations/troubleshooting. This README is a
quickstart and a tour of what makes ARICode worth using.

## Why ARICode

- **It verifies, it doesn't just trust.** An agent calling `finish` is not
  proof of success. ARICode re-runs the project's real checks itself —
  no LLM involved in the verification step — and distinguishes "verified,"
  "no tests to run yet," "a check kept failing," "a fix attempt changed
  nothing observable," and "the run never reached a coherent stopping
  point," instead of collapsing all of that into a single pass/fail. This
  exists because it kept catching real bugs a naive "trust the agent" loop
  missed — see `ROADMAP.md`'s known-limitations log for the (increasingly
  entertaining) history of exactly how.
- **Language-neutral verification**, not just Python. Detects and verifies
  Python, Node/JS/TS, Go, Rust, and Java (Maven/Gradle) projects — running
  each ecosystem's real test/build/lint/typecheck commands, never inventing
  one a project doesn't configure.
- **Genuinely model-agnostic.** Anthropic, OpenAI, Gemini, DeepSeek,
  Moonshot/Kimi, or a fully local/offline model via Ollama (Llama, Gemma,
  Qwen, Qwen-Coder, GLM) — picking a provider is one line in `.env`, never
  a code change, and never hardcoded anywhere in the source.
- **Deterministic automatic model selection**, if you want it. Hand-curate
  a catalog of candidate models with cost/quality ratings in `models.yaml`,
  and ARICode scores and ranks them per task (a debugging task weighted
  toward reasoning, a scaffolding task weighted toward cost — no extra LLM
  call to decide), with automatic fallback to the next-best candidate on a
  provider failure. Toggle models on/off with a single `activated: false`
  without deleting them from the catalog.
- **Real safety rails, not just a system prompt.** A hard iteration cap, a
  shared wall-clock task budget spanning every retry, task-tracker
  completion enforcement (an agent can't quietly leave half its own
  declared to-do list undone and still call `finish`), and an optional
  confirm-before-every-tool-call mode.
- **Run it your way**: a one-shot CLI, a local sandbox or a Docker
  container, a REST + WebSocket server for async/streaming integrations, or
  an OpenAI-compatible `/v1/chat/completions` endpoint that drops into
  tooling already built for that API.
- **Full observability.** Opt into a per-run artifacts directory holding
  the complete message transcript plus real token/cost metrics (including a
  per-model breakdown when auto-selection escalates through a fallback
  chain), and a human-readable `MODEL_DECISIONS.md` audit trail of exactly
  which model was picked, why, and what it fell back to.
- **Extensible by design.** Drop in a custom tool (terminal/file-editing
  plus whatever you add), or a new skill (project-specific or shared across
  every run) — no framework to fight, just files in the right folder.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for the virtual environment and
  dependencies
- An API key for at least one supported provider — or nothing at all if
  you run a fully local model via [Ollama](https://ollama.com)
- Docker, only if you plan to use `HARNESS_EXECUTION=docker`

## Install & setup

```bash
git clone https://github.com/alessioricco/ARICode.git
cd ARICode

cp .env.example .env          # then fill in LLM_MODEL and LLM_API_KEY
uv pip install -e ".[dev]"    # installs openhands-sdk, openhands-tools, and dev tools
```

`openhands-sdk` and `openhands-tools` are a matched set — always
install/upgrade them together at the same version (see `pyproject.toml`).
Optional extras for server mode, sandboxed Docker execution, and each
task-store backend are listed in `pyproject.toml`'s
`[project.optional-dependencies]` and documented in `MANUAL.md`.

Verify the install:

```bash
uv run pytest -q       # runs with no API key needed — nothing here calls a real LLM
```

## Run it

```bash
uv run python -m harness "Refactor utils.py to remove duplication, then run the tests."

# A named project, in its own subfolder under ./projects/, run in Docker
uv run python -m harness "Scaffold a FastAPI service with a /health endpoint." \
  --project my-api --execution docker

# Let ARICode pick the best-fit model from your models.yaml catalog
uv run python -m harness "Fix the failing test in payments.py" --auto-model
```

`uv pip install -e .` also installs `harness`, `harness-server`, and
`harness-admin` as regular console scripts, so `harness "<task>"` works the
same way without the `uv run python -m` prefix once your venv is active.

Full flag/env-var reference, the projects/execution-mode model, server
mode, and how to add a custom tool: **[`MANUAL.md`](MANUAL.md)**.

## Switching model / provider

Edit only `LLM_MODEL` in `.env` — no code changes. See
[`MANUAL.md`](MANUAL.md#switching-llm-provider--model) for supported
prefixes and the `LLM_BASE_URL` gotcha, and
[`MANUAL.md`](MANUAL.md#automatic-model-selection) for the optional
`models.yaml`-driven automatic selection described above
(`models.yaml.example` is a ready-to-copy starting catalog).

## How it works, briefly

ARICode builds config, custom tools, and policy **on top of** the OpenHands
SDK — it doesn't reimplement the agent loop, tool protocol, or
model-agnostic LLM routing (LiteLLM under the hood). The harness's own job
is everything the SDK doesn't opinionate about: which model runs and when
it falls back, what "actually verified" means for a given project, safety
budgets, and the CLI/server/OpenAI-compatible interfaces you talk to it
through. See `docs/SPEC.md` for the original build plan and `ROADMAP.md`
for the full, still-growing decisions log behind every non-obvious choice.

## Status

Config, LLM/tools/agent/runner wiring, a custom tool, the CLI, Docker
execution, and an HTTP/WebSocket server mode are all implemented and tested
— see `MANUAL.md` for usage. **For current milestone status, the
optional-feature backlog, and known limitations, see
[`ROADMAP.md`](ROADMAP.md)** — it's the up-to-date tracker; don't rely on
this paragraph to stay current.

## Building with Claude Code

This project was built by Claude Code following `docs/SPEC.md`, milestone
by milestone. Project config lives in `CLAUDE.md` (memory/guardrails) and
`.claude/` (permissions + the `/next-milestone`, `/verify-sdk`, `/add-tool`
commands). `ROADMAP.md` is the living status/backlog/decisions tracker that
goes with it — check it before assuming what's done. Start with
`docs/KICKOFF.md`.

## Layout

```
CLAUDE.md            # Claude Code memory + guardrails
MANUAL.md            # full usage guide — kept up to date with every change
ROADMAP.md           # status/backlog/decisions memory — kept up to date with every change
docs/SPEC.md         # the build plan (milestones)
docs/KICKOFF.md      # how to drive the build
.claude/             # permissions + slash commands
docker/              # agent-server.Dockerfile (docker execution image)
skills/              # shared skill catalog, loaded into every agent (see MANUAL.md)
src/harness/         # the harness package, incl. server.py (server mode)
tests/               # mirrors src/harness/
projects/            # generated software, one subfolder per --project (git-ignored)
```

## License

[MIT](LICENSE) — see `LICENSE` for the full text.
