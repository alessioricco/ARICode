"""Assembles the tools available to the agent: the SDK's default preset
(terminal, file editor, task tracker) plus our custom tools.
"""

from __future__ import annotations

from openhands.sdk.tool import Tool
from openhands.tools.preset.default import get_default_tools

from .custom_tools.run_tests_tool import build_run_tests_tool


def build_tools() -> list[Tool]:
    tools = list(get_default_tools(enable_browser=False))
    tools.append(build_run_tests_tool())
    return tools
