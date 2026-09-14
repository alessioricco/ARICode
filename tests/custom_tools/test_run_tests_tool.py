"""Executor tests for RunTestsTool: construct an Action, call the executor
directly, assert on the Observation. No LLM, no network — runs a real pytest
subprocess against a throwaway fixture directory, not this project's suite.
"""

from __future__ import annotations

import sys

from harness.custom_tools.run_tests_tool import RunTestsAction, RunTestsExecutor, _python_command

_FIXTURE = """
def test_ok():
    assert True

def test_broken():
    assert 1 == 2, "boom"
"""


def _write_fixture(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text(_FIXTURE)
    return test_file


def test_reports_pass_and_fail_counts(tmp_path):
    _write_fixture(tmp_path)
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.exit_code == 1
    assert observation.passed == 1
    assert observation.failed == 1
    assert observation.errors == 0
    assert observation.failures == ["test_fixture.py::test_broken - AssertionError: boom"]
    assert "1 failed" in observation.summary
    assert "1 passed" in observation.summary


def test_can_target_a_single_node_id(tmp_path):
    _write_fixture(tmp_path)
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction(path="test_fixture.py::test_ok"))

    assert observation.exit_code == 0
    assert observation.passed == 1
    assert observation.failed == 0
    assert observation.failures == []


def test_all_passing_has_no_failures(tmp_path):
    (tmp_path / "test_fixture.py").write_text("def test_ok():\n    assert True\n")
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.exit_code == 0
    assert observation.passed == 1
    assert observation.failed == 0
    assert observation.errors == 0
    assert observation.failures == []
    assert "1 passed" in observation.summary


def test_python_command_uses_sys_executable_when_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert _python_command() == [sys.executable]


def test_python_command_falls_back_to_path_when_frozen(monkeypatch):
    # Regression test: sys.executable inside a PyInstaller-frozen process (the
    # Docker agent-server image) resolves to the frozen binary itself, not a
    # Python interpreter — using it there silently re-invoked the agent-server
    # binary instead of running pytest.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "python3" else None)

    assert _python_command() == ["/usr/bin/python3"]
