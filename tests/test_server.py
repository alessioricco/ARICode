"""Server tests. No LLM/network: run_task/stream_task are monkeypatched, same
pattern as test_cli.py. Skips cleanly when the optional "server" extra
(fastapi/uvicorn/httpx) isn't installed.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from harness import server
from harness.config import Config, ConfigError
from harness.runner import CompletionContract, TaskOutcome


def _fake_outcome(verification_state: str = "verified", limitations=()) -> TaskOutcome:
    """The `TaskOutcome` `stream_task` now returns — fakes standing in for
    it in these tests need to return one instead of `None` so `server.py`'s
    `outcome.verification_state` access doesn't blow up."""
    contract = CompletionContract(
        goal="do something",
        acceptance_criteria=[],
        verification_checks=[],
        limitations=list(limitations),
    )
    return TaskOutcome(verification_state=verification_state, completion_contract=contract)


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

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["task"] = task
        calls["cfg"] = cfg
        on_message(_FakeMessage("assistant", "all done"))
        return _fake_outcome()

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
    assert final["verification_state"] == "verified"
    assert final["completion_contract"] is not None
    assert calls["task"] == "do something"
    assert calls["cfg"].execution == "local"


def test_create_task_with_project_creates_subfolder(monkeypatch, tmp_path):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["cfg"] = cfg
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something", "project": "myapp"})
    task_id = response.json()["task_id"]
    _wait_for_status(client, task_id)

    expected = str(tmp_path / "myapp")
    assert calls["cfg"].workspace == expected
    assert (tmp_path / "myapp").is_dir()


def test_create_task_rejects_a_project_path_escape_attempt(monkeypatch, tmp_path):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("stream_task should not be called")

    monkeypatch.setattr(server, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(server, "stream_task", _fail_if_called)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something", "project": "/etc/cron.d"})

    assert response.status_code == 400
    assert "must be relative, not absolute" in response.json()["detail"]


def test_create_task_agents_md_without_project_returns_400(monkeypatch):
    monkeypatch.setattr(server, "load_config", lambda: _cfg())

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something", "agents_md": "content"})

    assert response.status_code == 400
    assert "agents_md requires project" in response.json()["detail"]


def test_create_task_agents_md_with_project_writes_file(monkeypatch, tmp_path):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg(projects_dir=str(tmp_path)))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post(
        "/tasks",
        json={
            "task": "do something",
            "project": "myapp",
            "agents_md": "This project uses FastAPI.",
        },
    )
    _wait_for_status(client, response.json()["task_id"])

    assert (tmp_path / "myapp" / "AGENTS.md").read_text() == "This project uses FastAPI."


def test_create_task_with_model_override_swaps_llm_without_env(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["cfg"] = cfg
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post(
        "/tasks",
        json={
            "task": "do something",
            "model": "openai/gpt-4o",
            "api_key": "sk-other",
            "base_url": "http://localhost:11434",
        },
    )
    _wait_for_status(client, response.json()["task_id"])

    assert calls["cfg"].model == "openai/gpt-4o"
    assert calls["cfg"].api_key == "sk-other"
    assert calls["cfg"].base_url == "http://localhost:11434"


def test_create_task_with_reasoning_effort_override(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["cfg"] = cfg
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post(
        "/tasks",
        json={"task": "do something", "reasoning_effort": "low"},
    )
    _wait_for_status(client, response.json()["task_id"])

    assert calls["cfg"].reasoning_effort == "low"
    assert calls["cfg"].model == "anthropic/claude-x"


def test_create_task_without_override_keeps_configured_model(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["cfg"] = cfg
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    response = client.post("/tasks", json={"task": "do something"})
    _wait_for_status(client, response.json()["task_id"])

    assert calls["cfg"].model == "anthropic/claude-x"


def test_create_app_raises_immediately_on_a_config_error(monkeypatch):
    # load_config() is now called once at create_app() time (to build the
    # task store), not deferred to the first request — a config error
    # (missing LLM_MODEL, etc.) fails fast at server startup instead of
    # silently starting a server that only reveals the problem later.
    def _raise() -> Config:
        raise ConfigError("LLM_MODEL is required")

    monkeypatch.setattr(server, "load_config", _raise)

    with pytest.raises(ConfigError, match="LLM_MODEL is required"):
        server.create_app()


def test_get_task_reports_failure(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]

    final = _wait_for_status(client, task_id)
    assert final["status"] == "failed"
    assert "boom" in final["error"]
    assert final["final_message"] is None


def test_get_task_status_completed_does_not_imply_verification_passed(monkeypatch):
    # `status` only ever meant "the run didn't raise" — a task whose
    # verification retries were exhausted still ends with status
    # "completed" (no exception), so a poller must check
    # `verification_state`, not just `status`, to know whether it actually
    # succeeded. See MANUAL.md "Test verification".
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(verification_state="retry_exhausted", limitations=["pytest: 2 failed"])

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "completed"
    assert final["verification_state"] == "retry_exhausted"
    assert final["completion_contract"]["limitations"] == ["pytest: 2 failed"]


# --- require_verification: opt-in strict treatment of "inconclusive" ------


def test_create_task_inconclusive_defaults_to_completed_status(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(
            verification_state="inconclusive",
            limitations=["No automated check could be run for this project."],
        )

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "completed"
    assert final["verification_state"] == "inconclusive"


def test_create_task_require_verification_turns_unknown_project_into_failure(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(
            verification_state="inconclusive",
            limitations=["No automated check could be run for this project."],
        )

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks", json={"task": "do something", "require_verification": True}
    ).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "failed"
    assert final["verification_state"] == "inconclusive"
    assert "No automated check could be run" in final["error"]


def test_create_task_require_verification_turns_a_missing_tool_into_failure(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(
            verification_state="inconclusive",
            limitations=["npm is required to verify this project but was not found on PATH."],
        )

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks", json={"task": "do something", "require_verification": True}
    ).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "failed"


def test_create_task_require_verification_turns_no_tests_collected_into_failure(monkeypatch):
    # The quiet "pytest collected zero tests" case is still just
    # "inconclusive" from the caller's point of view — require_verification
    # doesn't special-case it.
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(verification_state="inconclusive", limitations=[])

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks", json={"task": "do something", "require_verification": True}
    ).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "failed"


def test_create_task_require_verification_does_not_affect_a_real_verified_pass(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(verification_state="verified")

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks", json={"task": "do something", "require_verification": True}
    ).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "completed"


def test_create_task_require_verification_does_not_change_other_verification_states(monkeypatch):
    # require_verification only special-cases "inconclusive" — every other
    # verification_state keeps its existing status semantics unchanged
    # ("completed" means the run didn't raise, never "verification passed";
    # see test_get_task_status_completed_does_not_imply_verification_passed).
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome(verification_state="retry_exhausted", limitations=["pytest: 2 failed"])

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks", json={"task": "do something", "require_verification": True}
    ).json()["task_id"]

    final = _wait_for_status(client, task_id)

    assert final["status"] == "completed"
    assert final["error"] is None  # unchanged: no require_verification message stapled on
    assert final["verification_state"] == "retry_exhausted"


def test_stream_task_require_verification_sends_error_event_for_inconclusive(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        on_message(_FakeMessage("assistant", "done"))
        return _fake_outcome(
            verification_state="inconclusive",
            limitations=["No automated check could be run for this project."],
        )

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something", "require_verification": True})
        ws.receive_json()  # the "message" event
        result = ws.receive_json()

    assert result["type"] == "error"
    assert "No automated check could be run" in result["detail"]


# --- acceptance_checks: opt-in machine-checkable acceptance criteria -------


def test_create_task_rejects_invalid_acceptance_checks(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("stream_task should not be called")

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fail_if_called)

    client = TestClient(server.create_app())
    response = client.post(
        "/tasks",
        json={
            "task": "do something",
            "acceptance_checks": [{"kind": "run_command", "path": "x"}],
        },
    )

    assert response.status_code == 400
    assert "Unknown acceptance check kind" in response.json()["detail"]


def test_create_task_passes_parsed_acceptance_checks_to_stream_task(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["acceptance_checks"] = acceptance_checks
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks",
        json={
            "task": "do something",
            "acceptance_checks": [{"kind": "file_exists", "path": "OUTPUT.txt"}],
        },
    ).json()["task_id"]
    _wait_for_status(client, task_id)

    assert calls["acceptance_checks"][0].path == "OUTPUT.txt"


def test_create_task_without_acceptance_checks_passes_none(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["acceptance_checks"] = acceptance_checks
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]
    _wait_for_status(client, task_id)

    assert calls["acceptance_checks"] is None


def test_get_task_exposes_acceptance_results(monkeypatch):
    from harness.acceptance import AcceptanceCheck, AcceptanceCheckResult

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        contract = CompletionContract(
            goal="do something", acceptance_criteria=[], verification_checks=[], limitations=[]
        )
        return TaskOutcome(
            verification_state="verified",
            completion_contract=contract,
            acceptance_results=(
                AcceptanceCheckResult(
                    check=AcceptanceCheck(kind="file_exists", path="OUTPUT.txt"),
                    passed=True,
                    detail="OUTPUT.txt exists",
                ),
            ),
        )

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]
    final = _wait_for_status(client, task_id)

    assert final["acceptance_results"] == [
        {
            "check": {
                "kind": "file_exists",
                "path": "OUTPUT.txt",
                "contains": None,
                "required": True,
                "description": None,
            },
            "passed": True,
            "detail": "OUTPUT.txt exists",
        }
    ]


def test_get_task_acceptance_results_is_none_when_not_supplied(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]
    final = _wait_for_status(client, task_id)

    assert final["acceptance_results"] is None


def test_stream_task_result_event_includes_acceptance_results(monkeypatch):
    from harness.acceptance import AcceptanceCheck, AcceptanceCheckResult

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        contract = CompletionContract(
            goal="do something", acceptance_criteria=[], verification_checks=[], limitations=[]
        )
        return TaskOutcome(
            verification_state="acceptance_failed",
            completion_contract=contract,
            acceptance_results=(
                AcceptanceCheckResult(
                    check=AcceptanceCheck(kind="file_exists", path="OUTPUT.txt"),
                    passed=False,
                    detail="OUTPUT.txt does not exist",
                ),
            ),
        )

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something"})
        result = ws.receive_json()

    assert result["type"] == "result"
    assert result["verification_state"] == "acceptance_failed"
    assert result["acceptance_results"][0]["passed"] is False


def test_get_task_unknown_id_returns_404():
    client = TestClient(server.create_app())

    response = client.get("/tasks/does-not-exist")

    assert response.status_code == 404


def test_create_task_with_project_field_is_retrievable(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post(
        "/tasks", json={"task": "do something", "project": "acme-website"}
    ).json()["task_id"]

    _wait_for_status(client, task_id)
    # `project` isn't echoed on GET /tasks/{id} today (no caller needs it
    # back), but it must have been stored — proven indirectly via
    # delete-by-project actually finding and removing this task below.
    delete_response = client.delete("/tasks", params={"project": "acme-website"})
    assert delete_response.status_code == 200
    assert delete_response.json() == {"project": "acme-website", "deleted": 1}
    assert client.get(f"/tasks/{task_id}").status_code == 404


def test_delete_task_removes_it(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    task_id = client.post("/tasks", json={"task": "do something"}).json()["task_id"]
    _wait_for_status(client, task_id)

    response = client.delete(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json() == {"task_id": task_id, "deleted": True}
    assert client.get(f"/tasks/{task_id}").status_code == 404


def test_delete_task_unknown_id_returns_404():
    client = TestClient(server.create_app())

    response = client.delete("/tasks/does-not-exist")

    assert response.status_code == 404


def test_delete_tasks_by_project_only_removes_matching_project(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    kept_id = client.post("/tasks", json={"task": "t1", "project": "keep-me"}).json()["task_id"]
    doomed_id = client.post("/tasks", json={"task": "t2", "project": "delete-me"}).json()["task_id"]
    _wait_for_status(client, kept_id)
    _wait_for_status(client, doomed_id)

    response = client.delete("/tasks", params={"project": "delete-me"})

    assert response.status_code == 200
    assert response.json() == {"project": "delete-me", "deleted": 1}
    assert client.get(f"/tasks/{doomed_id}").status_code == 404
    assert client.get(f"/tasks/{kept_id}").status_code == 200


def test_delete_tasks_by_project_unknown_project_returns_zero():
    client = TestClient(server.create_app())

    response = client.delete("/tasks", params={"project": "never-existed"})

    assert response.status_code == 200
    assert response.json() == {"project": "never-existed", "deleted": 0}


def test_get_task_reflects_partial_progress(monkeypatch):
    started = threading.Event()
    finish = threading.Event()

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        on_message(_FakeMessage("assistant", "step one"))
        started.set()
        finish.wait(timeout=2)
        on_message(_FakeMessage("assistant", "step two"))
        return _fake_outcome()

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
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        on_message(_FakeMessage("assistant", "step one"))
        on_message(_FakeMessage("assistant", "done"))
        return _fake_outcome()

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


def test_stream_task_sends_a_final_result_event_with_verification_state(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        on_message(_FakeMessage("assistant", "done"))
        return _fake_outcome(verification_state="retry_exhausted", limitations=["pytest: 2 failed"])

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something"})
        ws.receive_json()  # the "message" event
        result = ws.receive_json()

    assert result["type"] == "result"
    assert result["verification_state"] == "retry_exhausted"
    assert result["completion_contract"]["limitations"] == ["pytest: 2 failed"]


def test_stream_task_with_model_override_swaps_llm(monkeypatch):
    calls = {}

    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        calls["cfg"] = cfg
        on_message(_FakeMessage("assistant", "done"))
        return _fake_outcome()

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something", "model": "openai/gpt-4o"})
        ws.receive_json()

    assert calls["cfg"].model == "openai/gpt-4o"


def test_stream_task_per_request_config_error_sends_error_and_closes(monkeypatch):
    # load_config() itself succeeds at create_app() startup (see
    # test_create_app_raises_immediately_on_a_config_error for that case)
    # — this covers a per-request config problem inside _resolve_cfg
    # still surfacing cleanly as a WS error rather than an unhandled
    # exception, the same way POST /tasks already does.
    monkeypatch.setattr(server, "load_config", lambda: _cfg())

    client = TestClient(server.create_app())
    with client.websocket_connect("/tasks/stream") as ws:
        ws.send_json({"task": "do something", "agents_md": "content"})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "agents_md requires project" in message["detail"]


# --- OpenAI-compatible /v1/... ---


def test_narrative_texts_skips_system_and_user_only():
    # Regression test: an earlier version filtered to role == "assistant",
    # which silently dropped everything — live testing showed the SDK puts
    # human-readable text (including the final "finish" message) on `tool`-
    # role messages, not `assistant`-role ones (those carry empty content +
    # tool_calls instead).
    messages = [
        _FakeMessage("system", "system prompt"),
        _FakeMessage("user", "hi"),
        _FakeMessage("assistant", "hello"),
        _FakeMessage("tool", "some tool output"),
        _FakeMessage("assistant", "done"),
    ]

    assert server._narrative_texts(messages) == ["hello", "some tool output", "done"]


def test_task_from_chat_messages_concatenates_system_and_last_user():
    class _Msg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    messages = [
        _Msg("system", "You are a helpful assistant."),
        _Msg("user", "first question"),
        _Msg("assistant", "first answer"),
        _Msg("user", "second question"),
    ]

    task = server._task_from_chat_messages(messages)

    assert task == "You are a helpful assistant.\n\nsecond question"


def test_task_from_chat_messages_requires_a_user_message():
    class _Msg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    with pytest.raises(ValueError, match="at least one 'user' message"):
        server._task_from_chat_messages([_Msg("system", "context only")])


def test_list_models_reflects_configured_model(monkeypatch):
    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))

    client = TestClient(server.create_app())
    response = client.get("/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["id"] == "anthropic/claude-x"


def test_chat_completions_returns_openai_shaped_response(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["task"] = task
        calls["cfg"] = cfg
        # Matches reality (confirmed live): the agent's human-readable
        # "finish" text arrives as a tool-role message, not assistant-role.
        return [_FakeMessage("tool", "The answer is 4.")]

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "What is 2+2?"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o"
    assert body["choices"][0]["message"]["content"] == "The answer is 4."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert calls["task"] == "Be terse.\n\nWhat is 2+2?"
    assert calls["cfg"].execution == "local"


def test_chat_completions_llm_model_override_swaps_model_not_wire_field(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return [_FakeMessage("tool", "The answer is 4.")]

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "llm_model": "openai/gpt-4o",
            "llm_api_key": "sk-other",
        },
    )

    assert response.status_code == 200
    # The OpenAI wire field is still just echoed back...
    assert response.json()["model"] == "gpt-4o"
    # ...while the actual run used the llm_* override, not request.model.
    assert calls["cfg"].model == "openai/gpt-4o"
    assert calls["cfg"].api_key == "sk-other"


def test_chat_completions_llm_reasoning_effort_override(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return [_FakeMessage("tool", "The answer is 4.")]

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "llm_reasoning_effort": "xhigh",
        },
    )

    assert response.status_code == 200
    assert calls["cfg"].reasoning_effort == "xhigh"
    assert calls["cfg"].model == "anthropic/claude-x"


def test_chat_completions_without_llm_override_keeps_configured_model(monkeypatch):
    calls = {}

    def _fake_run_task(task, cfg=None):
        calls["cfg"] = cfg
        return [_FakeMessage("tool", "hi")]

    monkeypatch.setattr(server, "load_config", lambda: _cfg(model="anthropic/claude-x"))
    monkeypatch.setattr(server, "run_task", _fake_run_task)

    client = TestClient(server.create_app())
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert calls["cfg"].model == "anthropic/claude-x"


def test_chat_completions_without_user_message_returns_400(monkeypatch):
    monkeypatch.setattr(server, "load_config", lambda: _cfg())

    client = TestClient(server.create_app())
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "system", "content": "context only"}]},
    )

    assert response.status_code == 400


def test_chat_completions_per_request_config_error_returns_400(monkeypatch):
    # Same reframing as the WS test above: load_config() itself succeeds at
    # startup; this covers a per-request config problem (an absolute
    # `project`, rejected by resolve_project_dir — ChatCompletionRequest
    # has no agents_md field to reuse the WS test's trigger) still
    # returning a clean 400 rather than an unhandled exception.
    monkeypatch.setattr(server, "load_config", lambda: _cfg())

    client = TestClient(server.create_app())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "project": "/etc/cron.d",
        },
    )

    assert response.status_code == 400
    assert "must be relative, not absolute" in response.json()["detail"]


def test_chat_completions_streaming_sends_sse_chunks(monkeypatch):
    def _fake_stream_task(task, cfg=None, on_message=None, acceptance_checks=None):
        on_message(_FakeMessage("user", "echoed task, should not stream"))
        on_message(_FakeMessage("assistant", "step one"))
        # The real "finish" text arrives as a tool-role message (confirmed
        # live) — it must stream, unlike system/user echoes.
        on_message(_FakeMessage("tool", "step two"))

    monkeypatch.setattr(server, "load_config", lambda: _cfg())
    monkeypatch.setattr(server, "stream_task", _fake_stream_task)

    client = TestClient(server.create_app())
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "go"}], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line.startswith("data: ")]

    payloads = [line[len("data: ") :] for line in lines]
    assert payloads[-1] == "[DONE]"

    chunks = [json.loads(p) for p in payloads[:-1]]
    deltas = [c["choices"][0]["delta"] for c in chunks]

    assert deltas[0] == {"role": "assistant"}
    assert {"content": "step one"} in deltas
    assert {"content": "step two"} in deltas
    assert not any(d.get("content") == "echoed task, should not stream" for d in deltas)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
