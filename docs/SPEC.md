# Build Spec — AriCode on the OpenHands SDK

The canonical build plan for this repo. Claude Code implements it milestone by
milestone (section 11). Project memory and guardrails are in ../CLAUDE.md.

> IMPORTANT: the OpenHands SDK is young (public since Nov 2025) and its API still
> moves. Verify symbols against the live docs and the SDK's `examples/` before
> use (links in section 13). Treat exact tool class names and executor signatures
> as verify-first; prefer `get_default_tools()`.

---

## 1. Goal

A coding/software agent harness: given a task, it uses tools (terminal, file
editing, plus our custom tools) in a loop until done. The LLM provider and model
are chosen entirely via `.env` — switching provider is a one-line change, never a
code change.

## 2. Decision (settled)

Build on the **OpenHands Software Agent SDK** (MIT-licensed, purpose-built for
coding agents). It provides the agent loop, tool protocol, model-agnostic
multi-LLM routing (LiteLLM under the hood), optional Docker/remote sandboxing, a
REST/WebSocket server, and an OpenAI-compatible endpoint. We do not reimplement
any of these — we build config, custom tools, an interface, and policy.

## 3. Dependencies & install

`openhands-sdk` and `openhands-tools` are a **matched set** — install/upgrade both
in one command at the same version.

```bash
pip install -U openhands-sdk openhands-tools python-dotenv
pip install -U pytest ruff            # dev
# Optional (only if HARNESS_EXECUTION=docker later):
# pip install -U openhands-workspace openhands-agent-server
```

Pin exact versions in `pyproject.toml` once a working version is confirmed.

## 4. Configuration (the model-agnostic core)

See `.env.example` for the canonical list. The SDK reads `LLM_MODEL` /
`LLM_API_KEY` / `LLM_BASE_URL` via `LLM.load_from_env()`. Our own `HARNESS_*`
vars wrap policy that isn't the LLM's (workspace, iteration cap, confirm mode,
execution mode). Switching provider = edit `LLM_MODEL` only.

## 5. Project structure

```
aricode/
├── CLAUDE.md
├── README.md
├── .gitignore
├── .env.example
├── pyproject.toml
├── docs/
│   ├── SPEC.md            # this file
│   └── KICKOFF.md
├── .claude/
│   ├── settings.json
│   └── commands/          # /next-milestone, /verify-sdk, /add-tool
└── src/harness/
    ├── __init__.py
    ├── config.py          # load HARNESS_* env into a Config dataclass
    ├── llm.py             # build the LLM via LLM.load_from_env (+ validation)
    ├── tools.py           # assemble built-in + custom tools
    ├── agent.py           # build_agent(): wire LLM + tools + policy
    ├── runner.py          # run_task(task) -> result; owns Conversation lifecycle
    ├── custom_tools/
    │   ├── __init__.py
    │   └── example_tool.py
    └── cli.py             # `python -m harness "<task>"`
```
Tests mirror this under `tests/`.

## 6. Core wiring

> The code below was verified against the live docs (getting-started + the full
> custom-tools example) on the date this spec was written. `config.py` +
> `tests/test_config.py` are already implemented and passing (Milestone 1 done).

`config.py` — **already implemented** (pure env parsing, no SDK). Exposes
`Config` (frozen dataclass: model, api_key, base_url, workspace, max_iterations,
confirm_mode, execution), `ConfigError`, and `load_config(env=None)`.

`llm.py`
```python
from pydantic import SecretStr
from openhands.sdk import LLM
from .config import Config

def build_llm(cfg: Config) -> LLM:
    # Provider is encoded in cfg.model's prefix (LiteLLM convention:
    # "anthropic/...", "openai/gpt-4o", "gemini/...", "ollama/...").
    return LLM(
        usage_id="harness",
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=SecretStr(cfg.api_key) if cfg.api_key else None,
    )
```

`tools.py`
```python
from openhands.tools.preset import get_default_tools
from .custom_tools.example_tool import register_example_tool

def build_tools() -> list:
    # get_default_tools() -> terminal, file editor, task tracker, browser, MCP.
    tools = list(get_default_tools())
    tools.append(register_example_tool())   # returns Tool(name="ExampleTool")
    return tools
```

`agent.py`
```python
from openhands.sdk import Agent
from .config import Config
from .llm import build_llm
from .tools import build_tools

def build_agent(cfg: Config) -> Agent:
    agent = Agent(llm=build_llm(cfg), tools=build_tools())
    # If cfg.confirm_mode == "always", attach a confirmation policy that pauses
    # before each tool call (verify the current policy API via /verify-sdk).
    return agent
```

`runner.py` — result is captured via an event callback (there is **no**
`conversation.result`):
```python
from openhands.sdk import Conversation, Event, LLMConvertibleEvent
from .agent import build_agent
from .config import load_config

def run_task(task: str) -> list:
    cfg = load_config()
    messages: list = []

    def on_event(event: Event) -> None:
        if isinstance(event, LLMConvertibleEvent):
            messages.append(event.to_llm_message())

    conversation = Conversation(
        agent=build_agent(cfg),
        callbacks=[on_event],
        workspace=cfg.workspace,
    )
    conversation.send_message(task)
    conversation.run()
    return messages   # last message is the final assistant output
```

## 7. Custom tools (where our value lives)

Follow the SDK's Action / Observation / Executor pattern; mirror
`examples/01_standalone_sdk/02_custom_tools.py`.

`custom_tools/example_tool.py` (template — verified shape; replace with a real tool):
```python
from collections.abc import Sequence

from pydantic import Field
from openhands.sdk import (
    Action, Observation, TextContent, ImageContent, ToolDefinition,
)
from openhands.sdk.tool import Tool, ToolExecutor, register_tool


class ExampleAction(Action):
    query: str = Field(description="What to do")


class ExampleObservation(Observation):
    result: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        # How the result is shown to the model.
        return [TextContent(text=self.result or "No result.")]


class ExampleExecutor(ToolExecutor[ExampleAction, ExampleObservation]):
    def __call__(self, action: ExampleAction, conversation=None) -> ExampleObservation:
        # ... do the work ...
        return ExampleObservation(result=f"handled: {action.query}")


def _make_example_tool(conv_state) -> list[ToolDefinition]:
    # Factory receives conv_state -> access to conv_state.workspace.working_dir, etc.
    return [
        ToolDefinition(
            name="ExampleTool",
            description="One-line description the model reads to decide when to use it.",
            action_type=ExampleAction,
            observation_type=ExampleObservation,
            executor=ExampleExecutor(),
        )
    ]


def register_example_tool() -> Tool:
    register_tool("ExampleTool", _make_example_tool)
    return Tool(name="ExampleTool")
```
> Executor logic is unit-tested directly: construct an `ExampleAction`, call the
> executor, assert on the `ExampleObservation` — no LLM, no network.

Real tools to consider later: a test-runner that parses failures, a codebase
indexer/searcher, a project-specific linter, a deploy/PR trigger.

## 8. Execution mode & safety

- **Local (default):** tools run in the current process/workspace.
- **Docker/remote:** gate behind `HARNESS_EXECUTION=docker`; use
  `openhands-workspace` / `openhands-agent-server` per the remote-execution guide.
- **Confirmation:** `HARNESS_CONFIRM_MODE=always` attaches a policy that pauses
  before each tool call.
- Keep API keys in env only; never place secrets in prompts, tool args, or logs.

## 9. Optional enhancements (later; ask before starting)

- Repo context via `AgentContext` microagents/skills instead of a bloated prompt.
- Server mode (REST/WebSocket) or the OpenAI-compatible endpoint for IDE clients.
- Event streaming via the agent's `on_event` callback for logging/UI.

## 10. Testing

- `test_config.py`: HARNESS_* parsing, defaults, invalid values.
- `test_*_tool.py`: each custom tool's executor via a constructed Action; no LLM.
- `test_runner.py`: end-to-end smoke test that SKIPS when no API key is present;
  when a key exists, runs a tiny cheap task (e.g. create HELLO.txt).

## 11. Milestones (build order)

1. ~~Scaffold: `pyproject.toml`, `.env.example`, `config.py` + `test_config.py`.~~
   **DONE** — `config.py` and `tests/test_config.py` are implemented and passing
   (15 tests). Start at Milestone 2. (`pyproject.toml` still needs its SDK
   versions pinned once a working release is confirmed — see section 3.)
2. `llm.py` + `tools.py` + `agent.py` + `runner.py` with built-in tools only;
   get a hello-world task running end to end against one provider.
3. Prove provider-swap: change only `LLM_MODEL`, re-run against a second provider
   (and a local model via `LLM_BASE_URL` if available).
4. Custom-tool template + one real custom tool + its test.
5. CLI (`python -m harness "<task>"`), README, execution-mode flag.
6. (Optional) Docker execution, microagents/skills, server mode.

## 12. Acceptance criteria

- Switching provider/model requires editing only `LLM_MODEL` (+ the right key).
- The agent completes a multi-step coding task (edit files + run commands) e2e.
- At least one custom tool is registered and invoked by the agent.
- `HARNESS_MAX_ITERATIONS` reliably bounds runaway loops.
- Custom-tool tests pass with no network; runner smoke test skips without a key.
- README documents `.env`, provider switching (incl. a local model), and adding a tool.

## 13. Reference links

- SDK docs: https://docs.openhands.dev/sdk
- Getting started: https://docs.openhands.dev/sdk/getting-started
- Custom tools: https://docs.openhands.dev/sdk/guides/custom-tools
- Repo: https://github.com/OpenHands/software-agent-sdk
- Technical report: https://arxiv.org/abs/2511.03690
