"""Runs a task end to end and returns/streams the conversation's messages.

Result is captured via an event callback — there is no `conversation.result`.
"""

from __future__ import annotations

from collections.abc import Callable

from openhands.sdk import Conversation, Event, LLMConvertibleEvent, Message

from .agent import build_agent
from .config import Config, load_config
from .workspace import build_workspace


def stream_task(task: str, cfg: Config | None = None, on_message: Callable[[Message], None] | None = None) -> None:
    """Run a task, invoking `on_message` with each message as it's produced.

    Shared by `run_task` (collects into a list) and the server's WebSocket
    endpoint (pushes each message to the client as it arrives).
    """
    if cfg is None:
        cfg = load_config()
    emit = on_message or (lambda _msg: None)

    def on_event(event: Event) -> None:
        if isinstance(event, LLMConvertibleEvent):
            emit(event.to_llm_message())

    with build_workspace(cfg) as workspace:
        conversation = Conversation(
            agent=build_agent(cfg),
            callbacks=[on_event],
            workspace=workspace,
        )
        conversation.send_message(task)
        conversation.run()


def run_task(task: str, cfg: Config | None = None) -> list:
    messages: list = []
    stream_task(task, cfg=cfg, on_message=messages.append)
    return messages  # last message is the final assistant output
