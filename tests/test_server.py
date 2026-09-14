"""Server tests. No LLM/network: run_task/stream_task are monkeypatched, same
pattern as test_cli.py. Skips cleanly when the optional "server" extra
(fastapi/uvicorn/httpx) isn't installed.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from harness import server  # noqa: E402
from harness.config import Config, ConfigError  # noqa: E402


def _wait_for_status(client, task_id, *, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/tasks/{task_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not finish within {timeout}s")


def _cfg(**overrides) -> Config:
    base = dict(
        model="openai/gpt-4o",
        api_key="key",
        base_url=None,
        workspace=".",
        max_iterations=10,
        confirm_mode="never",
        execution="local",
    )
    base.update(overrides)
    return Config(**base)


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.content = [_FakeContent(text)]

    def model_dump(self, mode: str = "python") -> dict:
        return {"role": self.role, "content": [{"text": c.text} for c in self.content]}


def test_health():
    client = TestClient(server.create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_task_returns_immediately_with_pending_status(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None):
        calls["task"] = task
        calls["cfg"] = cfg
        on_message(_FakeMessage("assistant", "all done"))

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] in ("pending", "running", "completed")
    task_id = body["task_id"]

    final = _wait_for_status(client, task_id)
    assert final["status"] == "completed"
    assert final["final_message"] == "all done"
    assert calls["task"] == "do something"
    assert calls["cfg"].execution == "local"


def test_create_task_with_project_creates_subfolder(monkeypatch, tmp_path):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None):
        calls["cfg"] = cfg

    monkeypatch.setattr(server, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something", "project": "myapp"})
    task_id = response.json()["task_id"]
    _wait_for_status(client, task_id)

    expected = str(tmp_path / "myapp")
    assert calls["cfg"].workspace == expected
    assert (tmp_path / "myapp").is_dir()


def test_create_task_config_error_returns_400_immediately(monkeypatch):
    def _raise() -> Config:
        raise ConfigError("LLM_MODEL is required")

    monkeypatch.setattr(server, "load_config", _raise)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something"})

    assert response.status_code == 400
    assert "LLM_MODEL is required" in response.json()["detail"]


def test_get_task_reports_failure(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]

    final = _wait_for_status(client, task_id)
    assert final["status"] == "failed"
    assert "boom" in final["error"]
    assert final["final_message"] is None


def test_get_task_unknown_id_returns_404():
    client = TestClient(server.create_app())

    response = client.get("/tasks/does-not-exist")

    assert response.status_code == 404


def test_get_task_reflects_partial_progress(monkeypatch):
    started = threading.Event()
    finish = threading.Event()

    def _fake_stream_task(task, cfg=None, on_message=None):
        on_message(_FakeMessage("assistant", "step one"))
        started.set()
        finish.wait(timeout=2)
        on_message(_FakeMessage("assistant", "step two"))

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]

    assert started.wait(timeout=2), "background task never reached the first message"
    mid = client.get(f"/tasks/{task_id}").json()
    assert mid["status"] == "running"
    assert len(mid["messages"]) == 1

    finish.set()
    final = _wait_for_status(client, task_id)
    assert len(final["messages"]) == 2


def test_stream_task_sends_messages_then_closes(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None):
        on_message(_FakeMessage("assistant", "step one"))
        on_message(_FakeMessage("assistant", "done"))

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something"})
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "message"
    assert first["content"][0]["text"] == "step one"
    assert second["content"][0]["text"] == "done"


def test_stream_task_config_error_sends_error_and_closes(monkeypatch):
    def _raise() -> Config:
        raise ConfigError("LLM_MODEL is required")

    monkeypatch.setattr(server, "load_config", _raise)

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something"})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "LLM_MODEL is required" in message["detail"]
