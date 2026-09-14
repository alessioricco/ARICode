# Kickoff — running this build in Claude Code

This repo is already seeded with `CLAUDE.md`, `docs/SPEC.md`, `.claude/` config,
and the folder skeleton. You do **not** need to run `/init` — the memory file is
written. Just start Claude Code in the project root and drive it milestone by
milestone.

## First session prompt (paste this)

> Read CLAUDE.md and docs/SPEC.md. Confirm you understand the goal, the golden
> rules, and the milestone plan, then implement **Milestone 1** only: create
> `pyproject.toml` (pinning `openhands-sdk` and `openhands-tools` together),
> confirm `.env.example`, and implement `src/harness/config.py` with
> `tests/test_config.py`. Run `pytest -q` and report results. Do not start
> Milestone 2.

## Then, for each subsequent milestone

Use the slash command:

> /next-milestone

or target a specific one:

> /next-milestone 3

## Before using any unfamiliar OpenHands SDK API

> /verify-sdk <symbol or topic>

e.g. `/verify-sdk Conversation result accessor` or
`/verify-sdk get_default_tools import path`.

## To add a custom tool

> /add-tool a tool that runs pytest and returns parsed failures

## Guardrails already in place

- `.claude/settings.json` lets Claude Code run python/pip/uv/pytest/ruff/git
  without prompting, asks before `git push`, and denies reading `.env`.
- `CLAUDE.md` enforces: build on the SDK (don't reinvent), verify SDK symbols
  before use, keep the model-agnostic invariant (provider switch = `.env` only),
  and never read/print secrets.

## Sanity check after Milestone 2

Prove the headline requirement before going further: run the same task twice,
changing only `LLM_MODEL` in `.env` between runs (e.g. an Anthropic model, then
an OpenAI or local model). Same harness behavior, no code change = success.

## Tips

- Keep sessions scoped to one milestone; commit after each green test run.
- If imports fail with `ModuleNotFoundError: openhands.sdk.*`, reinstall
  `openhands-sdk` and `openhands-tools` together at the same version.
- Model IDs drift — if a model string errors, `/verify-sdk` the current ID.
