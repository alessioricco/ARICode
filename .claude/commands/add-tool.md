Add a new custom tool to the harness: $ARGUMENTS

$ARGUMENTS describes what the tool should do.

1. First confirm the current custom-tool API (Action / Observation / Executor /
   registration) against the canonical example — see @CLAUDE.md "SDK references"
   or run /verify-sdk custom tool Action Observation Executor.
2. Create a new module under `src/harness/custom_tools/` named for the tool
   (snake_case). Implement:
   - an Action subclass defining validated inputs (pydantic Fields with descriptions),
   - an Observation subclass defining structured output,
   - an Executor implementing the logic,
   - a `build_<name>_tool()` factory returning the registered Tool.
3. Register the tool in `src/harness/tools.py` so `build_tools()` includes it.
4. Add `tests/test_<name>_tool.py` that constructs an Action, calls the executor,
   and asserts the Observation — no LLM, no network.
5. Run `pytest -q`. Report the tool's name, inputs/outputs, and test results.

Keep the executor self-contained and side-effect-honest (a tool that writes files
or runs commands should say so in its description).
