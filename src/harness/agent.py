"""Wires the LLM and tools into an SDK Agent."""

from __future__ import annotations

from openhands.sdk import Agent

from .config import Config
from .llm import build_llm
from .tools import build_tools


def build_agent(cfg: Config) -> Agent:
    agent = Agent(llm=build_llm(cfg), tools=build_tools())
    # HARNESS_CONFIRM_MODE == "always" should attach a confirmation policy that
    # pauses before each tool call. The confirmation-policy API is not yet
    # verified against this SDK version (see CLAUDE.md) — /verify-sdk before
    # wiring it up.
    return agent
