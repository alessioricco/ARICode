# CLAUDE.md — Coding-Agent Harness

Project memory for Claude Code. Read this first, every session.

## What this project is

A **coding/software agent harness built on the OpenHands Software Agent SDK**.
Give it a task; it uses tools (terminal, file editing, plus our custom tools) in a
loop until the task is done. The LLM provider/model is chosen entirely via `.env`
so it stays **model-agnostic**.

The full build plan lives in @docs/SPEC.md. Follow it milestone by milestone
(section 11 of the spec). The kickoff prompt is in @docs/KICKOFF.md.

**Milestone 1 is already done:** `src/harness/config.py` and
`tests/test_config.py` are implemented and passing (15 tests). Start at
Milestone 2.

**These four SDK APIs are already verified** against the live docs — the code in
spec section 6/7 is correct, use it as written (no need to re-verify these):
`LLM(usage_id=..., model=..., base_url=..., api_key=SecretStr(...))`;
`get_default_tools()` from `openhands.tools.preset`; result capture via a
`Conversation(callbacks=[...])` callback filtering `LLMConvertibleEvent` and
calling `.to_llm_message()` (there is no `conversation.result`); and the custom
tool `Action`/`Observation`/`Executor` + `register_tool` + `Tool(name=...)`
pattern. Still `/verify-sdk` anything NOT in that list (e.g. the confirmation
policy API, Docker workspace API).

## The golden rules

1. **Build ON the OpenHands SDK. Do not reinvent it.** The agent loop, tool
   protocol, provider routing (LiteLLM under the hood), and sandboxing are the
   SDK's job. Our code is only: config, custom tools, an interface, and policy.
2. **Verify SDK symbols before using them.** The OpenHands SDK is young and its
   API still shifts. Before using any SDK class/function you have not already
   confirmed in this repo, check the live docs or the SDK's own `examples/`
   directory (see "SDK references" below). Prefer `get_default_tools()` over
   importing individual tool classes by name. Use `/verify-sdk` for this.
3. **Preserve the model-agnostic invariant.** Switching provider must require
   editing only `LLM_MODEL` in `.env` — never a code change. Do not hardcode a
   model, provider, or base URL anywhere in `src/`.
4. **Never read or print secrets.** `.env` holds real keys and is git-ignored and
   permission-denied. Use `.env.example` for structure. Never echo key values.

## Tech stack

- Python 3.12+
- `openhands-sdk` + `openhands-tools` — a **matched set**: always install/upgrade
  both in one command at the same version, or imports break.
- `python-dotenv` (config), `pytest` (tests), `ruff` (lint/format).
- Package uses a `src/` layout; code lives in `src/harness/`.

## Environment

This project uses **uv** for the virtual environment and dependencies. Run every
Python command through `uv run ...`, which uses the project's `.venv`
automatically — no manual activation needed. Install deps with
`uv pip install -e ".[dev]"` (resolves everything from `pyproject.toml`). Never
install into system Python — it's PEP 668 externally-managed and will error. If a
command seems to hit the wrong interpreter, or an install fails with
"externally-managed-environment", something bypassed uv: stop and ask me rather
than reaching for `--break-system-packages` or `sudo`.

## Commands

`uv run` executes inside the project venv automatically.

```bash
# Setup — installs the harness + all deps from pyproject.toml:
# openhands-sdk & openhands-tools (same version), python-dotenv, and dev tools.
uv pip install -e ".[dev]"

# Run the harness
uv run python -m harness "your task here"

# Test / lint
uv run pytest -q
uv run ruff check . && uv run ruff format .
```

Add new dependencies to `pyproject.toml` (don't `pip install` them ad hoc), and
keep `openhands-sdk`/`openhands-tools` pinned to the same version.

## Code conventions

- Small, single-purpose modules matching the layout in @docs/SPEC.md section 5.
- Custom tools follow the SDK's **Action / Observation / Executor** pattern
  (spec section 7); mirror `examples/01_standalone_sdk/02_custom_tools.py`.
- Each custom tool's executor must be unit-testable with a constructed Action and
  **no LLM/network call**.
- Type-hint public functions. Keep prompts and task text out of source — pass them
  in at call time.

## Testing rules

- `tests/` mirrors `src/harness/`.
- Tool executors are tested directly (no LLM).
- Any end-to-end test that needs a real API key must **skip cleanly when the key
  is absent**, so CI passes without secrets.
- Run `uv run pytest -q` after each milestone; do not move on with failing tests.

## Working style

- Implement one milestone at a time (spec section 11). After each: run tests,
  then briefly report what changed and what's next. Use `/next-milestone`.
- After milestone 2, prove the model-agnostic invariant: run the same task with
  two different `LLM_MODEL` values (spec milestone 3) before continuing.
- Ask before adding heavy optional pieces (Docker execution, server mode) — they
  are section 9 / "optional", not part of the core.

## Gotchas

- `openhands-sdk` and `openhands-tools` version mismatch → `ModuleNotFoundError`
  on `openhands.sdk.*`. Fix with `uv pip install -e ".[dev]"` (or pin both to the
  same version in `pyproject.toml`).
- Model ID strings (e.g. `anthropic/claude-sonnet-4-5-20250929`) change over
  time — verify current IDs against provider + LiteLLM docs, don't assume.
- Individual built-in tool class names have varied across SDK versions — that's
  why the default path is `get_default_tools()`.

## SDK references (consult before using unconfirmed APIs)

- Getting started: https://docs.openhands.dev/sdk/getting-started
- Custom tools: https://docs.openhands.dev/sdk/guides/custom-tools
- Custom-tool example (canonical):
  https://github.com/OpenHands/software-agent-sdk/blob/main/examples/01_standalone_sdk/02_custom_tools.py
- Repo / all examples: https://github.com/OpenHands/software-agent-sdk
