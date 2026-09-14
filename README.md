# Coding-Agent Harness

A model-agnostic coding/software agent harness built on the
[OpenHands Software Agent SDK](https://docs.openhands.dev/sdk). Give it a task; it
uses tools (terminal, file editing, plus your own custom tools) in a loop until
the task is done. Switch LLM provider/model by editing one line in `.env`.

## Status

Milestones 1, 2, 4, and 5 (see `docs/SPEC.md` section 11) are implemented and
tested: config, LLM/tools/agent/runner wiring, a custom tool, and the CLI.
Milestone 3 (live proof of a provider swap against a second provider/local
model) is code-ready but not yet run live — it needs a second provider's key
or a local model endpoint. Built by Claude Code following `docs/SPEC.md`,
milestone by milestone; see `docs/KICKOFF.md` to start.

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
```

`HARNESS_WORKSPACE` in `.env` (default `.`) is the directory the agent operates
in. Note: the agent's file-editing tool requires absolute paths and does not
resolve relative ones against the workspace itself, so for reliable runs give
the model the absolute workspace path in the task text, or point
`HARNESS_WORKSPACE` at the directory you want and describe files relative to
that path explicitly in the task.

### Execution mode

`HARNESS_EXECUTION` (`.env`, default `local`) selects where tools run. Only
`local` is implemented — the agent's tools run in this process/workspace
directly. `docker` is reserved for future sandboxed/remote execution
(see `docs/SPEC.md` section 9, optional/not yet built) and is rejected with a
clear error if selected, rather than silently falling back to `local`. Override
per run with `--execution`:

```bash
uv run python -m harness "..." --execution local
```

## Switching model / provider

Edit only `LLM_MODEL` in `.env` — no code changes:

- `anthropic/claude-...` — Anthropic
- `openai/gpt-4o` — OpenAI
- `gemini/gemini-...` — Google
- `ollama/llama3` (+ `LLM_BASE_URL=http://localhost:11434`) — local model
- `openhands/claude-...` — OpenHands proxy

Model IDs drift over time; verify current strings against the provider +
LiteLLM docs if one errors. `LLM_BASE_URL` must be left with an empty value and
no trailing text — a value-then-comment on the same line parses fine, but a
comment-only line (blank value followed by `# comment`) is *not* stripped by
`python-dotenv` and would otherwise be read as the literal base URL.

## Adding a custom tool

Custom tools follow the SDK's Action / Observation / Executor pattern and live in
`src/harness/custom_tools/` — see `custom_tools/example_tool.py` for the
template and `custom_tools/run_tests_tool.py` for a real one (runs the project's
pytest suite and returns structured pass/fail results). In Claude Code, run
`/add-tool <description>`; see `docs/SPEC.md` section 7. Then register it in
`build_tools()` (`src/harness/tools.py`) so the agent picks it up.

## Building with Claude Code

Project config lives in `CLAUDE.md` (memory/guardrails) and `.claude/`
(permissions + the `/next-milestone`, `/verify-sdk`, `/add-tool` commands). Start
with `docs/KICKOFF.md`.

## Layout

```
CLAUDE.md            # Claude Code memory + guardrails
docs/SPEC.md         # the build plan (milestones)
docs/KICKOFF.md      # how to drive the build
.claude/             # permissions + slash commands
src/harness/         # the harness package (built by Claude Code)
tests/               # mirrors src/harness/
```
