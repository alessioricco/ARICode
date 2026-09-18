"""HTTP/WebSocket server: lets other tools (IDE clients, dashboards, scripts)
drive the harness over the network instead of only the CLI.

This is another interface on top of the same stream_task()/Config machinery
cli.py's run_task() wraps — not a second agent loop. Requires the `server` extra
(`uv pip install -e ".[server]"`), imported lazily so the base install stays
free of a FastAPI/uvicorn dependency.
"""

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .config import Config, ConfigError, load_config, override_llm, resolve_project_dir
from .runner import run_task, stream_task
from .skills import write_project_context

_SENTINEL = object()


def _narrative_texts(messages: list) -> list[str]:
    """Extract the agent's output narrative from real Message objects
    (run_task's return value): everything except the echoed `system` prompt
    and `user` task text.

    Confirmed live, not assumed: an `assistant`-role turn that makes a tool
    call has *empty* `content` (the call itself lives in `tool_calls`); the
    human-readable text — including the final "finish" message — comes back
    as a `tool`-role message's content. Filtering to `role == "assistant"`
    (the obvious-looking first attempt) silently drops everything, including
    the final answer.
    """
    texts = []
    for message in messages:
        if message.role in ("system", "user"):
            continue
        for content in message.content:
            text = getattr(content, "text", None)
            if text:
                texts.append(text)
    return texts


def _task_from_chat_messages(chat_messages: list) -> str:
    """Map OpenAI-style chat messages onto one harness task string.

    Every `system` message is concatenated as leading context; the *last*
    `user` message is the task itself. Prior `assistant` turns are not
    replayed — this harness's continuity story is the project's own
    workspace/skills (see MANUAL.md "Skills"), not chat history, and there is
    no cheap way to resume a previous agent loop mid-conversation.
    """
    system_parts = [m.content for m in chat_messages if m.role == "system"]
    user_parts = [m.content for m in chat_messages if m.role == "user"]
    if not user_parts:
        raise ValueError("messages must include at least one 'user' message")
    task = user_parts[-1]
    if system_parts:
        task = "\n\n".join([*system_parts, task])
    return task


def _resolve_cfg(
    *,
    execution: str | None,
    project: str | None,
    agents_md: str | None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
) -> Config:
    if agents_md is not None and project is None:
        raise ValueError("agents_md requires project")

    cfg = load_config()
    if (
        model is not None
        or api_key is not None
        or base_url is not None
        or reasoning_effort is not None
    ):
        cfg = override_llm(
            cfg, model=model, api_key=api_key, base_url=base_url, reasoning_effort=reasoning_effort
        )
    if execution is not None:
        cfg = replace(cfg, execution=execution)
    if project is not None:
        project_dir = resolve_project_dir(cfg.projects_dir, project)
        os.makedirs(project_dir, exist_ok=True)
        cfg = replace(cfg, workspace=project_dir)
    if agents_md is not None:
        write_project_context(cfg.workspace, agents_md)
    return cfg


def _final_text_from_dumps(messages: list[dict]) -> str | None:
    if not messages:
        return None
    for content in messages[-1].get("content", []):
        text = content.get("text")
        if text:
            return text
    return None


@dataclass
class _TaskRecord:
    """In-memory record for an async /tasks submission.

    Lives only in this process's memory — lost on restart, not shared across
    server instances. Fine for a single-process server; would need a real
    store (Redis, a DB) to survive restarts or scale to multiple workers.
    """

    id: str
    task: str
    status: str = "pending"  # pending -> running -> completed | failed
    messages: list[dict] = field(default_factory=list)
    error: str | None = None
    updated_at: float = field(default_factory=time.time)
    # `status == "completed"` only ever meant "the run ended without raising
    # an exception" — it said nothing about whether verification actually
    # passed. `verification_state`/`completion_contract` carry that (see
    # runner.py's TaskOutcome) so a poller doesn't mistake a
    # retry-exhausted or stuck run for a confirmed success just because
    # `status` says "completed".
    verification_state: str | None = None
    completion_contract: dict[str, Any] | None = None


def create_app():
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from fastapi.responses import StreamingResponse
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError(
            'Server mode requires the "server" extra: run `uv pip install -e ".[server]"`.'
        ) from exc

    class TaskRequest(BaseModel):
        task: str
        project: str | None = None
        execution: str | None = None
        agents_md: str | None = None
        # Per-request LLM overrides — swap provider/model for just this task
        # without touching .env (e.g. to compare models on the same task).
        model: str | None = None
        api_key: str | None = None
        base_url: str | None = None
        reasoning_effort: str | None = None

    class ChatMessage(BaseModel):
        role: str
        content: str

    class ChatCompletionRequest(BaseModel):
        model: str
        messages: list[ChatMessage]
        stream: bool = False
        # Harness extensions, ignored by strict OpenAI clients that don't send
        # them: same meaning as TaskRequest's fields.
        project: str | None = None
        execution: str | None = None
        # `model` above is the OpenAI wire field — always echoed back, never
        # used to pick a provider (see /v1/chat/completions docstring). These
        # are the actual per-request LLM overrides, kept separate so a strict
        # OpenAI client's `model` value (which may not be a LiteLLM-style
        # "provider/model" id) can never accidentally change what runs.
        llm_model: str | None = None
        llm_api_key: str | None = None
        llm_base_url: str | None = None
        llm_reasoning_effort: str | None = None

    app = FastAPI(title="Coding-Agent Harness")
    tasks: dict[str, _TaskRecord] = {}
    tasks_lock = threading.Lock()

    def run_in_background(record: _TaskRecord, cfg: Config) -> None:
        with tasks_lock:
            record.status = "running"
            record.updated_at = time.time()

        def on_message(message) -> None:
            with tasks_lock:
                record.messages.append(message.model_dump(mode="json"))
                record.updated_at = time.time()

        try:
            outcome = stream_task(record.task, cfg=cfg, on_message=on_message)
            with tasks_lock:
                record.status = "completed"
                record.verification_state = outcome.verification_state
                record.completion_contract = asdict(outcome.completion_contract)
        except Exception as exc:  # noqa: BLE001 - surfaced via GET /tasks/{id}, not raised here
            with tasks_lock:
                record.status = "failed"
                record.error = str(exc)
        finally:
            with tasks_lock:
                record.updated_at = time.time()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/tasks", status_code=202)
    def create_task(request: TaskRequest) -> dict[str, Any]:
        # Config errors fail fast, synchronously, before a task_id even exists —
        # only a run-time error (once the agent is actually working) becomes an
        # async "failed" status instead of an HTTP error.
        try:
            cfg = _resolve_cfg(
                execution=request.execution,
                project=request.project,
                agents_md=request.agents_md,
                model=request.model,
                api_key=request.api_key,
                base_url=request.base_url,
                reasoning_effort=request.reasoning_effort,
            )
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        record = _TaskRecord(id=str(uuid.uuid4()), task=request.task)
        with tasks_lock:
            tasks[record.id] = record
        threading.Thread(target=run_in_background, args=(record, cfg), daemon=True).start()

        return {"task_id": record.id, "status": record.status}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        with tasks_lock:
            record = tasks.get(task_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
            return {
                "task_id": record.id,
                "status": record.status,
                "final_message": (
                    _final_text_from_dumps(record.messages)
                    if record.status == "completed"
                    else None
                ),
                # See MANUAL.md "Test verification": `status == "completed"`
                # only means the run didn't raise — check `verification_state`
                # for whether it was actually confirmed working.
                "verification_state": record.verification_state,
                "completion_contract": record.completion_contract,
                "messages": list(record.messages),
                "error": record.error,
            }

    @app.websocket("/tasks/stream")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            payload = await websocket.receive_json()
            request = TaskRequest(**payload)
            cfg = _resolve_cfg(
                execution=request.execution,
                project=request.project,
                agents_md=request.agents_md,
                model=request.model,
                api_key=request.api_key,
                base_url=request.base_url,
                reasoning_effort=request.reasoning_effort,
            )
        except (ConfigError, ValueError) as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            await websocket.close()
            return

        events: queue.Queue = queue.Queue()

        def on_message(message) -> None:
            events.put({"type": "message", **message.model_dump(mode="json")})

        def run() -> None:
            try:
                outcome = stream_task(request.task, cfg=cfg, on_message=on_message)
                events.put(
                    {
                        "type": "result",
                        "verification_state": outcome.verification_state,
                        "completion_contract": asdict(outcome.completion_contract),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the client, not raised
                events.put({"type": "error", "detail": str(exc)})
            finally:
                events.put(_SENTINEL)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        loop = asyncio.get_event_loop()
        try:
            while True:
                item = await loop.run_in_executor(None, events.get)
                if item is _SENTINEL:
                    break
                await websocket.send_json(item)
        except WebSocketDisconnect:
            pass
        else:
            await websocket.close()

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        # Reflects the real, .env-configured model — there's nothing to pick
        # between server-side; see chat_completions()'s docstring.
        cfg = load_config()
        return {
            "object": "list",
            "data": [{"id": cfg.model, "object": "model", "owned_by": "coding-agent-harness"}],
        }

    @app.post("/v1/chat/completions")
    def chat_completions(request: ChatCompletionRequest):
        """OpenAI-compatible adapter over the same run_task()/stream_task().

        `request.model` is accepted (required by the wire format) and echoed
        back, but never used to select a provider — the model-agnostic
        invariant holds here too: the actual model defaults to whatever
        `.env`'s LLM_MODEL says, exactly like every other interface, and
        `request.model` isn't a safe stand-in for an explicit override since
        a strict OpenAI client's value there may not be a LiteLLM-style
        "provider/model" id at all. Callers who want to experiment with a
        different provider/model/reasoning-effort for a request, without
        touching `.env`, use the separate `llm_model`/`llm_api_key`/
        `llm_base_url`/`llm_reasoning_effort` extension fields instead — same
        override mechanism as `TaskRequest`'s `model`/`api_key`/`base_url`/
        `reasoning_effort`.

        Chat history isn't replayed: every `system` message is concatenated
        as leading context, the *last* `user` message is the task, and prior
        `assistant` turns are dropped — see `_task_from_chat_messages`'s
        docstring for why. `usage` is always zeroed; token counts aren't
        tracked across a whole agent loop today.
        """
        try:
            task = _task_from_chat_messages(request.messages)
            cfg = _resolve_cfg(
                execution=request.execution,
                project=request.project,
                agents_md=None,
                model=request.llm_model,
                api_key=request.llm_api_key,
                base_url=request.llm_base_url,
                reasoning_effort=request.llm_reasoning_effort,
            )
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        if not request.stream:
            try:
                messages = run_task(task, cfg=cfg)
            except Exception as exc:  # noqa: BLE001 - surfaced as a clean HTTP error
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            content = "\n\n".join(_narrative_texts(messages))
            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        def sse_chunk(delta: dict, finish_reason: str | None = None) -> str:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            return f"data: {json.dumps(payload)}\n\n"

        def event_stream():
            # A plain sync generator: Starlette's StreamingResponse iterates
            # it in a threadpool, which is exactly right since it blocks on
            # a synchronous queue.get() — no asyncio bridging needed here,
            # unlike the /tasks/stream WebSocket handler above.
            events: queue.Queue = queue.Queue()

            def on_message(message) -> None:
                events.put(message)

            def run() -> None:
                try:
                    stream_task(task, cfg=cfg, on_message=on_message)
                except Exception as exc:  # noqa: BLE001 - surfaced in-stream, not raised
                    events.put(exc)
                finally:
                    events.put(_SENTINEL)

            threading.Thread(target=run, daemon=True).start()

            yield sse_chunk({"role": "assistant"})
            while True:
                item = events.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    yield sse_chunk({"content": f"[error: {item}]"})
                    continue
                if item.role in ("system", "user"):
                    continue
                for content in item.content:
                    text = getattr(content, "text", None)
                    if text:
                        yield sse_chunk({"content": text})
            yield sse_chunk({}, finish_reason="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            'Server mode requires the "server" extra: run `uv pip install -e ".[server]"`.'
        ) from exc

    parser = argparse.ArgumentParser(
        prog="harness-server",
        description="Run the coding-agent harness as an HTTP/WebSocket server.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
