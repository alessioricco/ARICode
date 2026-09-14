# Coding-Agent Harness

A model-agnostic coding/software agent harness built on the
[OpenHands Software Agent SDK](https://docs.openhands.dev/sdk). Give it a task; it
uses tools (terminal, file editing, plus your own custom tools) in a loop until
the task is done. Switch LLM provider/model by editing one line in `.env`.

## Status

Seed repo. The implementation is built by Claude Code following `docs/SPEC.md`,
milestone by milestone. See `docs/KICKOFF.md` to start.

## Setup

```bash
cp .env.example .env          # then fill in LLM_MODEL and LLM_API_KEY
pip install -U openhands-sdk openhands-tools python-dotenv
pip install -U pytest ruff    # dev
```

`openhands-sdk` and `openhands-tools` are a matched set — always install/upgrade
them together at the same version.

## Run

```bash
python -m harness "Refactor utils.py to remove duplication, then run the tests."
```

## Switching model / provider

Edit only `LLM_MODEL` in `.env`:

- `anthropic/claude-...` — Anthropic
- `gpt-4o` — OpenAI
- `gemini/gemini-...` — Google
- `ollama/llama3` (+ `LLM_BASE_URL=http://localhost:11434`) — local
- `openhands/claude-...` — OpenHands proxy

No code changes. Model IDs drift over time; verify current strings against the
provider + LiteLLM docs if one errors.

## Adding a custom tool

Custom tools follow the SDK's Action / Observation / Executor pattern and live in
`src/harness/custom_tools/`. In Claude Code, run `/add-tool <description>`; see
`docs/SPEC.md` section 7.

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
