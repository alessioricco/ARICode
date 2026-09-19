"""admin_cli.py tests. No LLM/network — exercises the CLI against a real
sqlite-backed SQLTaskStore (via tmp_path), monkeypatching only `load_config`
the same way test_cli.py/test_server.py do.

Deliberately not `memory`: `build_task_store()` constructs a brand-new,
isolated `MemoryTaskStore` on every call, so a test that seeds one instance
and then has `admin_cli.main()` build another would never see the seeded
data — that isolation is correct in-process behavior for `memory` (see
task_store.py), just not usable for testing a CLI that builds its own store
per invocation. sqlite persists to a real file, so seeding and the CLI's
own store construction see the same data, same as they would against a
real shared redis/mysql/postgres deployment.
"""

from __future__ import annotations

import pytest

from harness import admin_cli
from harness.config import Config
from harness.task_store import TaskRecord, build_task_store

pytest.importorskip("sqlalchemy")


def _cfg(tmp_path, **overrides) -> Config:
    base = dict(
        model="openai/gpt-4o",
        api_key="key",
        base_url=None,
        workspace=".",
        max_iterations=10,
        confirm_mode="never",
        execution="local",
        task_store="sqlite",
        task_store_sqlite_path=str(tmp_path / "admin_cli_test.db"),
    )
    base.update(overrides)
    return Config(**base)


def _seed(cfg: Config, *records: TaskRecord) -> None:
    store = build_task_store(cfg)
    try:
        for record in records:
            store.save(record)
    finally:
        store.close()


def test_delete_project_removes_only_matching_tasks(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    _seed(
        cfg,
        TaskRecord(id="a", task="t", project="acme", status="completed"),
        TaskRecord(id="b", task="t", project="other", status="completed"),
    )
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["delete-project", "acme"])

    assert exc_info.value.code == 0
    assert "Deleted 1 task(s)" in capsys.readouterr().out

    store = build_task_store(cfg)
    assert store.get("a") is None
    assert store.get("b") is not None


def test_delete_project_unknown_project_reports_zero(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["delete-project", "never-existed"])

    assert exc_info.value.code == 0
    assert "Deleted 0 task(s)" in capsys.readouterr().out


def test_delete_task_removes_it(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    _seed(cfg, TaskRecord(id="a", task="t", status="completed"))
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["delete-task", "a"])

    assert exc_info.value.code == 0
    assert "Deleted task 'a'" in capsys.readouterr().out
    assert build_task_store(cfg).get("a") is None


def test_delete_task_unknown_id_exits_nonzero(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["delete-task", "does-not-exist"])

    assert exc_info.value.code == 1
    assert "No task found" in capsys.readouterr().out


def test_purge_with_ttl_override_removes_old_terminal_records(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    old = TaskRecord(id="a", task="t", status="completed")
    old.updated_at = 1.0  # far in the past
    _seed(cfg, old)
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["purge", "--ttl-seconds", "1"])

    assert exc_info.value.code == 0
    assert "Purged 1 expired task(s)" in capsys.readouterr().out
    assert build_task_store(cfg).get("a") is None


def test_purge_refuses_when_ttl_is_zero_and_unset(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path, task_ttl_seconds=0)
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["purge"])

    assert exc_info.value.code == 1
    assert "Refusing to purge" in capsys.readouterr().out


def test_show_prints_summary(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    _seed(cfg, TaskRecord(id="a", task="t", project="acme", status="completed"))
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["show", "a"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "id:                 a" in out
    assert "project:            acme" in out
    assert "status:             completed" in out


def test_show_unknown_id_exits_nonzero(monkeypatch, capsys, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(admin_cli, "load_config", lambda: cfg)

    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main(["show", "does-not-exist"])

    assert exc_info.value.code == 1
    assert "No task found" in capsys.readouterr().out


def test_no_command_exits_nonzero_with_usage():
    with pytest.raises(SystemExit) as exc_info:
        admin_cli.main([])

    assert exc_info.value.code != 0
