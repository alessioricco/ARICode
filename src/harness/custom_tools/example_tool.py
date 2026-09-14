"""Template for a custom tool — copy this file to add a new one.

Verified against openhands-sdk v1.47.0 (see /verify-sdk before relying on this
for a different version): `ToolDefinition` is subclassed, not instantiated
directly, and the class-level `name` is auto-derived from the class name
(CamelCase -> snake_case, trailing "_tool" stripped) unless set explicitly.
`register_tool` takes the class itself; it calls `.create(conv_state, **params)`
to build the tool. Mirrors the SDK's own
examples/01_standalone_sdk/02_custom_tools.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor, register_tool


class ExampleAction(Action):
    query: str = Field(description="What to do")


class ExampleObservation(Observation):
    result: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result or "No result.")]


class ExampleExecutor(ToolExecutor[ExampleAction, ExampleObservation]):
    def __call__(self, action: ExampleAction, conversation=None) -> ExampleObservation:
        return ExampleObservation(result=f"handled: {action.query}")


class ExampleTool(ToolDefinition[ExampleAction, ExampleObservation]):
    """One-line description the model reads to decide when to use it."""

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[ToolDefinition]:
        # conv_state gives access to conv_state.workspace.working_dir, etc.
        return [
            cls(
                description=cls.__doc__ or "",
                action_type=ExampleAction,
                observation_type=ExampleObservation,
                executor=ExampleExecutor(),
            )
        ]


def build_example_tool() -> Tool:
    register_tool(ExampleTool.name, ExampleTool)
    return Tool(name=ExampleTool.name)
