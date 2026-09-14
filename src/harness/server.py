"""HTTP/WebSocket server: lets other tools (IDE clients, dashboards, scripts)
drive the harness over the network instead of only the CLI.

This is another interface on top of the same stream_task()/Config machinery
cli.py's run_task() wraps — not a second agent loop. Requires the `server` extra
(`uv pip install -e ".[server]"`), imported lazily so the base install stays
free of a FastAPI/uvicorn dependency.
"""

import asyncio
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from .config import Config, ConfigError, load_config
from .runner import stream_task

_SENTINEL = object()


def _resolve_cfg(*, execution: str | None, project: str | None) -> Config:
    cfg = load_config()
    if execution is not None:
        cfg = replace(cfg, execution=execution)
    if project is not None:
        project_dir = os.path.abspath(os.path.join(cfg.projects_dir, project))
        os.makedirs(project_dir, exist_ok=True)
        cfg = replace(cfg, workspace=project_dir)
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


def create_app():
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError(
            'Server mode requires the "server" extra: run `uv pip install -e ".[server]"`.'
        ) from exc

    class TaskRequest(BaseModel):
        task: str
        project: str | None = None
        execution: str | None = None

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
            stream_task(record.task, cfg=cfg, on_message=on_message)
            with tasks_lock:
                record.status = "completed"
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
            cfg = _resolve_cfg(execution=request.execution, project=request.project)
        except ConfigError as exc:
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
                    _final_text_from_dumps(record.messages) if record.status == "completed" else None
                ),
                "messages": list(record.messages),
                "error": record.error,
            }

    @app.websocket("/tasks/stream")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            payload = await websocket.receive_json()
            request = TaskRequest(**payload)
            cfg = _resolve_cfg(execution=request.execution, project=request.project)
        except (ConfigError, ValueError) as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            await websocket.close()
            return

        events: queue.Queue = queue.Queue()

        def on_message(message) -> None:
            events.put({"type": "message", **message.model_dump(mode="json")})

        def run() -> None:
            try:
                stream_task(request.task, cfg=cfg, on_message=on_message)
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
