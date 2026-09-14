"""Wires the LLM, tools, and skill context into an SDK Agent."""

from __future__ import annotations

from openhands.sdk import Agent, AgentContext

from .config import Config
from .llm import build_llm
from .skills import load_skill_catalog
from .tools import build_tools


def build_agent(cfg: Config) -> Agent:
    agent_context = AgentContext(
        skills=load_skill_catalog(cfg.skills_dir),
        # Resolved lazily by the Conversation once the real workspace path is
        # known (AgentContext itself doesn't know it yet) — this is what makes
        # a project's AGENTS.md / .agents/skills/ (see skills.write_project_context)
        # apply automatically, on top of the shared catalog above.
        load_project_skills=True,
    )
    agent = Agent(llm=build_llm(cfg), tools=build_tools(), agent_context=agent_context)
    # HARNESS_CONFIRM_MODE == "always" should attach a confirmation policy that
    # pauses before each tool call. The confirmation-policy API is not yet
    # verified against this SDK version (see CLAUDE.md) — /verify-sdk before
    # wiring it up.
    return agent
