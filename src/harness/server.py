"""HTTP/WebSocket server: lets other tools (IDE clients, dashboards, scripts)
drive the harness over the network instead of only the CLI.

This is another interface on top of the same run_task()/stream_task()/Config
machinery cli.py uses — not a second agent loop. Requires the `server` extra
(`uv pip install -e ".[server]"`), imported lazily so the base install stays
free of a FastAPI/uvicorn dependency.
"""

import asyncio
import os
import queue
import threading
from dataclasses import replace
from typing import Any

from .config import Config, ConfigError, load_config
from .runner import run_task, stream_task

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


def _final_text(messages: list) -> str | None:
    if not messages:
        return None
    for content in messages[-1].content:
        text = getattr(content, "text", None)
        if text:
            return text
    return None


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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/tasks")
    def create_task(request: TaskRequest) -> dict[str, Any]:
        try:
            cfg = _resolve_cfg(execution=request.execution, project=request.project)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            messages = run_task(request.task, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clean HTTP error
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "final_message": _final_text(messages),
            "messages": [m.model_dump(mode="json") for m in messages],
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
