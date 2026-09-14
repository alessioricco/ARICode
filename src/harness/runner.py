"""Runs a task end to end and returns the conversation's messages.

Result is captured via an event callback — there is no `conversation.result`.
"""

from __future__ import annotations

from openhands.sdk import Conversation, Event, LLMConvertibleEvent

from .agent import build_agent
from .config import Config, load_config


def run_task(task: str, cfg: Config | None = None) -> list:
    if cfg is None:
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
    return messages  # last message is the final assistant output
