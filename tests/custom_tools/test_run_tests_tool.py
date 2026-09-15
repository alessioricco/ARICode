"""Executor tests for RunTestsTool: construct an Action, call the executor
directly, assert on the Observation. No LLM, no network — runs a real pytest
subprocess against a throwaway fixture directory, not this project's suite.
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from harness.custom_tools.run_tests_tool import (
    RunTestsAction,
    RunTestsExecutor,
    _detect_verification,
    _python_command,
)

_FIXTURE = """
def test_ok():
    assert True

def test_broken():
    assert 1 == 2, "boom"
"""

_requires_npm = pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")


def _write_fixture(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text(_FIXTURE)
    return test_file


def _write_package_json(tmp_path, scripts):
    (tmp_path / "package.json").write_text(json.dumps({"name": "fixture", "scripts": scripts}))


def test_reports_pass_and_fail_counts(tmp_path):
    _write_fixture(tmp_path)
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.check_kind == "pytest"
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


# --- Project-type detection (the generalization beyond pytest-only) --------
#
# Added after a live failure: a scaffolded Vite/React project had a JSX parse
# error in App.tsx, but pytest-only verification silently reported "no tests
# collected" (exit code 5, treated as "nothing to verify") for that same
# project — a false pass on code that didn't even compile. Detection must
# route a Node project with a `build` script to `npm run build` instead,
# which actually exercises the bundler/transpiler and catches that class of
# error.


def test_falls_back_to_pytest_without_any_project_markers(tmp_path):
    kind, run_dir = _detect_verification(str(tmp_path))

    assert kind == "pytest"
    assert run_dir == str(tmp_path)


def test_detects_npm_build_when_package_json_has_build_script(tmp_path):
    _write_package_json(tmp_path, {"build": "vite build"})

    kind, run_dir = _detect_verification(str(tmp_path))

    assert kind == "npm_build"
    assert run_dir == str(tmp_path)


def test_package_json_without_build_script_falls_back_to_pytest(tmp_path):
    _write_package_json(tmp_path, {"start": "node index.js"})

    kind, _run_dir = _detect_verification(str(tmp_path))

    assert kind == "pytest"


def test_python_markers_win_over_a_sibling_package_json(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    _write_package_json(tmp_path, {"build": "vite build"})

    kind, _run_dir = _detect_verification(str(tmp_path))

    assert kind == "pytest"


def test_finds_a_package_json_nested_below_the_workspace_root(tmp_path):
    # Confirmed live: `npm create vite@latest <name>` nested the actual app
    # under <project>/<name>/<name>/, one or two levels below the --project
    # workspace root.
    nested = tmp_path / "outer" / "inner"
    nested.mkdir(parents=True)
    _write_package_json(nested, {"build": "vite build"})

    kind, run_dir = _detect_verification(str(tmp_path))

    assert kind == "npm_build"
    assert run_dir == str(nested)


@_requires_npm
def test_run_npm_build_reports_success(tmp_path):
    _write_package_json(tmp_path, {"build": 'node -e "process.exit(0)"'})
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.check_kind == "npm_build"
    assert observation.exit_code == 0
    assert "succeeded" in observation.summary


@_requires_npm
def test_run_npm_build_reports_failure_with_raw_output(tmp_path):
    _write_package_json(
        tmp_path,
        {"build": 'node -e "console.error(\'PARSE_ERROR: boom\'); process.exit(1)"'},
    )
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.check_kind == "npm_build"
    assert observation.exit_code == 1
    assert "failed" in observation.summary
    assert "PARSE_ERROR" in observation.output


def test_run_npm_build_is_inconclusive_when_npm_is_missing(tmp_path, monkeypatch):
    _write_package_json(tmp_path, {"build": 'node -e "process.exit(0)"'})
    monkeypatch.setattr("shutil.which", lambda _name: None)
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.check_kind == "none"
