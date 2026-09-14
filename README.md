# Coding-Agent Harness

A model-agnostic coding/software agent harness built on the
[OpenHands Software Agent SDK](https://docs.openhands.dev/sdk). Give it a task; it
uses tools (terminal, file editing, plus your own custom tools) in a loop until
the task is done. Switch LLM provider/model by editing one line in `.env`.

**See [`MANUAL.md`](MANUAL.md) for the full usage guide** — configuration
reference, CLI flags, projects, execution modes (local/Docker), adding custom
tools, and known limitations/troubleshooting. This README is a quickstart.

## Status

Milestones 1, 2, 4, and 5 (see `docs/SPEC.md` section 11) are implemented and
tested: config, LLM/tools/agent/runner wiring, a custom tool, and the CLI.
Docker execution and an HTTP/WebSocket server mode (spec section 9,
originally optional) are also implemented — see `MANUAL.md`. Milestone 3
(live proof of a provider swap against a second provider/local model) is
code-ready but not yet run live — it needs a second provider's key or a local
model endpoint. Built by Claude Code following `docs/SPEC.md`, milestone by
milestone; see `docs/KICKOFF.md` to start.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for the venv and dependencies
— run every command through `uv run`, which uses `.venv` automatically.

```bash
cp .env.example .env          # then fill in LLM_MODEL and LLM_API_KEY
uv pip install -e ".[dev]"    # installs openhands-sdk, openhands-tools, and dev tools
```

`openhands-sdk` and `openhands-tools` are a matched set — always install/upgrade
them together at the same version (see `pyproject.toml`).

## Run

```bash
uv run python -m harness "Refactor utils.py to remove duplication, then run the tests."

# A named project, in its own subfolder under ./projects/, run in Docker
uv run python -m harness "Scaffold a FastAPI service with a /health endpoint." \
  --project my-api --execution docker
```

Full flag/env-var reference, the projects/execution-mode model, and how to
add a custom tool: **[`MANUAL.md`](MANUAL.md)**.

## Switching model / provider

Edit only `LLM_MODEL` in `.env` — no code changes. See
[`MANUAL.md`](MANUAL.md#switching-llm-provider--model) for supported
prefixes and the `LLM_BASE_URL` gotcha.

## Building with Claude Code

Project config lives in `CLAUDE.md` (memory/guardrails) and `.claude/`
(permissions + the `/next-milestone`, `/verify-sdk`, `/add-tool` commands). Start
with `docs/KICKOFF.md`.

## Layout

```
CLAUDE.md            # Claude Code memory + guardrails
MANUAL.md            # full usage guide — kept up to date with every change
docs/SPEC.md         # the build plan (milestones)
docs/KICKOFF.md      # how to drive the build
.claude/             # permissions + slash commands
docker/              # agent-server.Dockerfile (docker execution image)
src/harness/         # the harness package, incl. server.py (server mode)
tests/               # mirrors src/harness/
projects/            # generated software, one subfolder per --project (git-ignored)
```
