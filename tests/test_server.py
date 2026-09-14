"""Server tests. No LLM/network: run_task/stream_task are monkeypatched, same
pattern as test_cli.py. Skips cleanly when the optional "server" extra
(fastapi/uvicorn/httpx) isn't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from harness import server  # noqa: E402
from harness.config import Config, ConfigError  # noqa: E402


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


def test_create_task_returns_final_message(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["task"] = task
        calls["cfg"] = cfg
        return [_FakeMessage("assistant", "all done")]

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something"})

    assert response.status_code == 200
    body = response.json()
    assert body["final_message"] == "all done"
    assert calls["task"] == "do something"
    assert calls["cfg"].execution == "local"


def test_create_task_with_project_creates_subfolder(monkeypatch, tmp_path):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return []

    monkeypatch.setattr(server, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something", "project": "myapp"})

    expected = str(tmp_path / "myapp")
    assert response.status_code == 200
    assert calls["cfg"].workspace == expected
    assert (tmp_path / "myapp").is_dir()


def test_create_task_config_error_returns_400(monkeypatch):
    def _raise() -> Config:
        raise ConfigError("LLM_MODEL is required")

    monkeypatch.setattr(server, "load_config", _raise)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something"})

    assert response.status_code == 400
    assert "LLM_MODEL is required" in response.json()["detail"]


def test_create_task_run_error_returns_500(monkeypatch):
    def _fake_run_task(task, cfg=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something"})

    assert response.status_code == 500
    assert "boom" in response.json()["detail"]


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
