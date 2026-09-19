"""Tests for the language-neutral verification pipeline (detect -> discover
plan -> execute -> aggregate) plus the agent-facing RunTestsTool executor.
No LLM, no network — real subprocesses only for tools actually installed in
this dev environment (pytest, npm, ruff, go, cargo — each gated by a
skip-if-missing marker matching the project's own convention), everything
else exercised via constructed `CheckSpec`s or monkeypatched `shutil.which`.
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from harness.custom_tools.run_tests_tool import (
    CheckOutcome,
    CheckSpec,
    ProjectDetection,
    RunTestsAction,
    RunTestsExecutor,
    VerificationRun,
    _entrypoint_ordering_spec,
    _find_python_entrypoints,
    _mypy_configured,
    _npm_test_script,
    _python_command,
    _python_plan,
    _ruff_configured,
    detect_project,
    discover_verification_plan,
    execute_check,
    run_full_verification,
)

_FIXTURE = """
def test_ok():
    assert True

def test_broken():
    assert 1 == 2, "boom"
"""

_requires_npm = pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
_requires_go = pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")
_requires_cargo = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")


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
    monkeypatch.setattr(
        "shutil.which", lambda name: f"/usr/bin/{name}" if name == "python3" else None
    )

    assert _python_command() == ["/usr/bin/python3"]


def test_run_tests_tool_generic_path_covers_go(tmp_path, monkeypatch):
    # The agent-facing tool's contract (always exactly one check) must hold
    # for languages beyond Python/Node too — exercised here against a real
    # go.mod with `go` monkeypatched unavailable, so it's deterministic and
    # fast regardless of whether this environment has Go installed.
    (tmp_path / "go.mod").write_text("module example.com/thing\n\ngo 1.21\n")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.check_kind == "go"
    assert "not installed" in observation.summary


def test_run_tests_tool_generic_path_covers_ambiguous_monorepo(tmp_path):
    # The agent-facing tool must not silently pick one of several candidate
    # projects either — same generic (detect -> discover -> execute) path
    # as Go/Rust/Java above.
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    _write_package_json(frontend, {"build": "vite build"})
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname = 'backend'\n")
    executor = RunTestsExecutor(str(tmp_path))

    observation = executor(RunTestsAction())

    assert observation.check_kind == "ambiguous"
    assert "ambiguous" in observation.summary


# --- Stage 1: project detection ---------------------------------------------


def test_detects_python_via_pyproject_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="python", root=str(tmp_path))


def test_detects_python_via_bare_source_files_when_no_manifest(tmp_path):
    # Preserves the original always-try-pytest default: this repo's own
    # fixture tests, and plenty of small real scripts, have no
    # pyproject.toml at all.
    (tmp_path / "test_fixture.py").write_text("def test_ok():\n    assert True\n")

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="python", root=str(tmp_path))


def test_detects_node_via_package_json(tmp_path):
    _write_package_json(tmp_path, {"build": "vite build"})

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="node", root=str(tmp_path))


def test_detects_go_via_go_mod(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/thing\n\ngo 1.21\n")

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="go", root=str(tmp_path))


def test_detects_rust_via_cargo_toml(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "fixture"\nversion = "0.1.0"\n')

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="rust", root=str(tmp_path))


def test_detects_java_maven_via_pom_xml(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>\n")

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="java-maven", root=str(tmp_path))


def test_detects_java_gradle_via_build_gradle(tmp_path):
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n")

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="java-gradle", root=str(tmp_path))


def test_detects_java_gradle_via_kotlin_dsl(tmp_path):
    (tmp_path / "build.gradle.kts").write_text("plugins { java }\n")

    detection = detect_project(str(tmp_path))

    assert detection.language == "java-gradle"


def test_unknown_project_has_no_recognizable_markers(tmp_path):
    (tmp_path / "README.md").write_text("just some notes, no code here\n")

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="unknown", root=str(tmp_path))


def test_empty_directory_is_unknown(tmp_path):
    detection = detect_project(str(tmp_path))

    assert detection.language == "unknown"


def test_python_markers_win_over_a_sibling_package_json(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    _write_package_json(tmp_path, {"build": "vite build"})

    detection = detect_project(str(tmp_path))

    assert detection.language == "python"


def test_finds_a_marker_nested_below_the_workspace_root(tmp_path):
    # Confirmed live: `npm create vite@latest <name>` nested the actual app
    # under <project>/<name>/<name>/, one or two levels below the --project
    # workspace root.
    nested = tmp_path / "outer" / "inner"
    nested.mkdir(parents=True)
    _write_package_json(nested, {"build": "vite build"})

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="node", root=str(nested))


def test_sibling_projects_at_the_same_depth_are_ambiguous(tmp_path):
    # A monorepo/workspace with two unrelated applications, neither at the
    # workspace root — no single obvious project to verify.
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    _write_package_json(frontend, {"build": "vite build"})
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname = 'backend'\n")

    detection = detect_project(str(tmp_path))

    assert detection.language == "ambiguous"
    assert detection.root == str(tmp_path)
    assert detection.candidates is not None
    assert set(detection.candidates) == {
        ("node", str(frontend)),
        ("python", str(backend)),
    }


def test_ambiguous_candidates_are_reported_regardless_of_directory_listing_order(tmp_path):
    # Two candidates at the same depth, same languages, different names —
    # confirms the walk collects every match at the shallowest depth rather
    # than stopping at the first one a DFS happens to visit.
    app_a = tmp_path / "app-a"
    app_a.mkdir()
    _write_package_json(app_a, {"build": "vite build"})
    app_b = tmp_path / "app-b"
    app_b.mkdir()
    _write_package_json(app_b, {"build": "vite build"})

    detection = detect_project(str(tmp_path))

    assert detection.language == "ambiguous"
    assert set(detection.candidates) == {
        ("node", str(app_a)),
        ("node", str(app_b)),
    }


def test_explicit_root_with_its_own_manifest_is_not_ambiguous(tmp_path):
    # The workspace root itself is the intended project (e.g. HARNESS_WORKSPACE
    # /--project already points at it) — a nested manifest elsewhere (a
    # vendored dependency, an example) must not turn this into "ambiguous".
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    vendored = tmp_path / "vendor" / "some-lib"
    vendored.mkdir(parents=True)
    _write_package_json(vendored, {"build": "vite build"})

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="python", root=str(tmp_path))


def test_a_single_shallower_match_wins_over_deeper_sibling_matches(tmp_path):
    # Only the shallowest depth's matches are candidates — a single match
    # there is unambiguous even if more projects exist further down.
    (tmp_path / "app" / "pyproject.toml").parent.mkdir(parents=True)
    (tmp_path / "app" / "pyproject.toml").write_text("[project]\nname = 'app'\n")
    nested_a = tmp_path / "app" / "vendor" / "a"
    nested_a.mkdir(parents=True)
    _write_package_json(nested_a, {"build": "vite build"})
    nested_b = tmp_path / "app" / "vendor" / "b"
    nested_b.mkdir(parents=True)
    _write_package_json(nested_b, {"build": "vite build"})

    detection = detect_project(str(tmp_path))

    assert detection == ProjectDetection(language="python", root=str(tmp_path / "app"))


# --- Stage 2: verification-plan discovery (pure, no subprocess) ------------


def test_python_plan_has_pytest_as_the_only_primary_check(tmp_path):
    plan = discover_verification_plan(ProjectDetection(language="python", root=str(tmp_path)))

    assert [s.name for s in plan] == ["pytest"]
    assert plan[0].primary is True


def test_python_plan_adds_ruff_when_configured_and_available(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")

    plan = discover_verification_plan(ProjectDetection(language="python", root=str(tmp_path)))

    names = [s.name for s in plan]
    assert "ruff" in names
    ruff_spec = next(s for s in plan if s.name == "ruff")
    assert ruff_spec.primary is False
    assert ruff_spec.command is not None  # ruff is installed in this dev environment


def test_python_plan_marks_ruff_unavailable_when_not_installed(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    monkeypatch.setattr("shutil.which", lambda _name: None)

    plan = discover_verification_plan(ProjectDetection(language="python", root=str(tmp_path)))

    ruff_spec = next(s for s in plan if s.name == "ruff")
    assert ruff_spec.command is None
    assert "not installed" in ruff_spec.unavailable_reason


def test_python_plan_marks_mypy_unavailable_when_not_installed(tmp_path):
    # mypy genuinely isn't installed in this dev environment — a real,
    # not-monkeypatched exercise of the "configured but not installed" path.
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")

    plan = discover_verification_plan(ProjectDetection(language="python", root=str(tmp_path)))

    mypy_spec = next(s for s in plan if s.name == "mypy")
    assert mypy_spec.command is None
    assert shutil.which("mypy") is None  # confirms the test is exercising the real gap
    assert "not installed" in mypy_spec.unavailable_reason


def test_python_plan_has_no_lint_typecheck_when_unconfigured(tmp_path):
    plan = discover_verification_plan(ProjectDetection(language="python", root=str(tmp_path)))

    assert [s.name for s in plan] == ["pytest"]


def test_node_plan_prefers_build_as_primary_over_test(tmp_path):
    _write_package_json(tmp_path, {"build": "vite build", "test": "vitest run"})

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    primary = next(s for s in plan if s.primary)
    assert primary.name == "npm run build"
    assert {s.name for s in plan} == {"npm run build", "npm test"}


def test_node_plan_uses_test_as_primary_when_no_build_script(tmp_path):
    _write_package_json(tmp_path, {"test": "vitest run"})

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].name == "npm test"
    assert plan[0].primary is True


def test_node_plan_includes_lint_and_typecheck_when_defined(tmp_path):
    _write_package_json(
        tmp_path,
        {"build": "vite build", "lint": "eslint .", "typecheck": "tsc --noEmit"},
    )

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    assert {s.name for s in plan} == {"npm run build", "npm run lint", "npm run typecheck"}


def test_node_plan_accepts_type_check_hyphenated_key(tmp_path):
    _write_package_json(tmp_path, {"build": "vite build", "type-check": "tsc --noEmit"})

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    assert "npm run type-check" in {s.name for s in plan}


def test_node_plan_ignores_placeholder_test_script(tmp_path):
    _write_package_json(
        tmp_path, {"build": "vite build", "test": 'echo "Error: no test specified" && exit 1'}
    )

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    assert [s.name for s in plan] == ["npm run build"]


def test_node_plan_is_unavailable_with_no_build_or_test_script(tmp_path):
    _write_package_json(tmp_path, {"start": "node index.js"})

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].command is None
    assert "no build or test script" in plan[0].unavailable_reason


def test_node_plan_marks_every_check_unavailable_when_npm_missing(tmp_path, monkeypatch):
    _write_package_json(tmp_path, {"build": "vite build", "lint": "eslint ."})
    monkeypatch.setattr("shutil.which", lambda _name: None)

    plan = discover_verification_plan(ProjectDetection(language="node", root=str(tmp_path)))

    assert all(s.command is None for s in plan)
    assert all("npm not found" in s.unavailable_reason for s in plan)


def test_go_plan_has_test_and_vet(tmp_path):
    plan = discover_verification_plan(ProjectDetection(language="go", root=str(tmp_path)))

    names = {s.name: s.primary for s in plan}
    assert names == {"go test": True, "go vet": False}


def test_go_plan_unavailable_without_go_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)

    plan = discover_verification_plan(ProjectDetection(language="go", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].command is None
    assert "not installed" in plan[0].unavailable_reason


def test_rust_plan_has_test_check_and_clippy(tmp_path):
    plan = discover_verification_plan(ProjectDetection(language="rust", root=str(tmp_path)))

    names = {s.name: s.primary for s in plan}
    assert names == {"cargo test": True, "cargo check": False, "cargo clippy": False}


def test_rust_plan_unavailable_without_cargo_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)

    plan = discover_verification_plan(ProjectDetection(language="rust", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].command is None
    assert "not installed" in plan[0].unavailable_reason


def test_java_maven_plan_prefers_wrapper_over_global_mvn(tmp_path, monkeypatch):
    wrapper = tmp_path / "mvnw"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "mvn" else None)

    plan = discover_verification_plan(ProjectDetection(language="java-maven", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].command == [str(wrapper), "test"]


def test_java_maven_plan_falls_back_to_global_mvn_without_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/mvn" if name == "mvn" else None)

    plan = discover_verification_plan(ProjectDetection(language="java-maven", root=str(tmp_path)))

    assert plan[0].command == ["/usr/bin/mvn", "test"]


def test_java_maven_plan_unavailable_with_neither_wrapper_nor_mvn(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)

    plan = discover_verification_plan(ProjectDetection(language="java-maven", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].command is None
    assert "mvnw" in plan[0].unavailable_reason
    assert "mvn" in plan[0].unavailable_reason


def test_java_gradle_plan_prefers_wrapper_over_global_gradle(tmp_path, monkeypatch):
    wrapper = tmp_path / "gradlew"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    plan = discover_verification_plan(ProjectDetection(language="java-gradle", root=str(tmp_path)))

    assert plan[0].command == [str(wrapper), "test"]


def test_unknown_project_plan_is_a_single_unavailable_check(tmp_path):
    plan = discover_verification_plan(ProjectDetection(language="unknown", root=str(tmp_path)))

    assert len(plan) == 1
    assert plan[0].primary is True
    assert plan[0].command is None
    assert "No known project type" in plan[0].unavailable_reason


def test_ambiguous_project_plan_is_a_single_unavailable_check_naming_candidates(tmp_path):
    detection = ProjectDetection(
        language="ambiguous",
        root=str(tmp_path),
        candidates=(("node", str(tmp_path / "frontend")), ("python", str(tmp_path / "backend"))),
    )

    plan = discover_verification_plan(detection)

    assert len(plan) == 1
    assert plan[0].primary is True
    assert plan[0].command is None
    assert "ambiguous" in plan[0].unavailable_reason
    assert str(tmp_path / "frontend") in plan[0].unavailable_reason
    assert str(tmp_path / "backend") in plan[0].unavailable_reason


# --- Stage 3: command execution ---------------------------------------------


def test_execute_check_reports_passed(tmp_path):
    spec = CheckSpec(
        name="ok", primary=True, command=[sys.executable, "-c", "pass"], cwd=str(tmp_path)
    )

    outcome = execute_check(spec)

    assert outcome.status == "passed"
    assert outcome.exit_code == 0
    assert outcome.project_root == str(tmp_path)
    assert outcome.command == f"{sys.executable} -c pass"


def test_execute_check_reports_failed_with_exit_code_and_output(tmp_path):
    spec = CheckSpec(
        name="broken",
        primary=True,
        command=[sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"],
        cwd=str(tmp_path),
    )

    outcome = execute_check(spec)

    assert outcome.status == "failed"
    assert outcome.exit_code == 3
    assert "boom" in outcome.output


def test_execute_check_reports_skipped_via_skip_exit_code(tmp_path):
    spec = CheckSpec(
        name="pytest",
        primary=True,
        command=[sys.executable, "-c", "import sys; sys.exit(5)"],
        cwd=str(tmp_path),
        skip_exit_codes=(5,),
        skip_reason="no tests collected",
    )

    outcome = execute_check(spec)

    assert outcome.status == "skipped"
    assert outcome.summary == "no tests collected"


def test_execute_check_reports_skipped_when_command_is_none_with_skip_reason(tmp_path):
    spec = CheckSpec(
        name="lint", primary=False, command=None, cwd=str(tmp_path), skip_reason="not configured"
    )

    outcome = execute_check(spec)

    assert outcome.status == "skipped"
    assert outcome.exit_code is None
    assert outcome.summary == "not configured"


def test_execute_check_reports_unavailable_when_command_is_none_with_reason(tmp_path):
    spec = CheckSpec(
        name="mypy",
        primary=False,
        command=None,
        cwd=str(tmp_path),
        unavailable_reason="not installed",
    )

    outcome = execute_check(spec)

    assert outcome.status == "unavailable"
    assert outcome.summary == "not installed"


def test_execute_check_reports_unavailable_when_the_binary_does_not_exist(tmp_path):
    spec = CheckSpec(name="ghost", primary=True, command=["/no/such/binary-xyz"], cwd=str(tmp_path))

    outcome = execute_check(spec)

    assert outcome.status == "unavailable"
    assert "could not be run" in outcome.summary


def test_execute_check_reports_timed_out(tmp_path, monkeypatch):
    import subprocess

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd="sleep 999", timeout=kwargs.get("timeout", 1), output="partial output"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    spec = CheckSpec(name="slow", primary=True, command=["sleep", "999"], cwd=str(tmp_path))

    outcome = execute_check(spec, timeout=1)

    assert outcome.status == "timed_out"
    assert outcome.exit_code is None
    assert "timeout" in outcome.summary
    assert "partial output" in outcome.output


def test_execute_check_returns_a_precomputed_outcome_without_running_anything(
    tmp_path, monkeypatch
):
    import subprocess

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("execute_check must not spawn a subprocess for a precomputed spec")

    monkeypatch.setattr(subprocess, "run", _fail_if_called)
    precomputed = CheckOutcome(
        name="static",
        command="",
        primary=False,
        status="failed",
        exit_code=None,
        project_root=str(tmp_path),
        summary="static check failed",
        output="details",
    )
    spec = CheckSpec(
        name="static", primary=False, command=None, cwd=str(tmp_path), precomputed=precomputed
    )

    assert execute_check(spec) is precomputed


def test_execute_check_closes_stdin_so_a_reading_subprocess_fails_fast_instead_of_hanging(tmp_path):
    # Regression guard for the entry-point smoke-test check below: without
    # stdin=DEVNULL, a script that calls input() would inherit whatever
    # stdin this test process has, which can hang a check indefinitely
    # instead of failing fast.
    spec = CheckSpec(
        name="reads-stdin",
        primary=True,
        command=[sys.executable, "-c", "input()"],
        cwd=str(tmp_path),
    )

    outcome = execute_check(spec, timeout=10)

    assert outcome.status == "failed"  # EOFError on closed stdin, not a hang
    assert outcome.exit_code != 0


# --- Python entry-point checks: catches a bug pytest structurally cannot ---
#
# A passing test suite only proves the code works when *imported* — a
# function defined after a module's own `if __name__ == "__main__":` guard
# exists by the time anything imports the module (the whole file runs
# top-to-bottom, guard included, and __name__ != "__main__" on import so the
# guarded call to main() never happens), but does not exist yet when the
# script is actually run directly and immediately calls into that guard.


_ORDERING_BUG_SOURCE = (
    "def main():\n"
    "    print('start')\n"
    "    helper()\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
    "\n"
    "def helper():\n"
    "    print('should have run')\n"
)

_ORDERING_OK_SOURCE = (
    "def helper():\n"
    "    print('should have run')\n"
    "\n"
    "def main():\n"
    "    print('start')\n"
    "    helper()\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def test_is_main_guard_recognizes_the_standard_form(tmp_path):
    (tmp_path / "app.py").write_text("if __name__ == '__main__':\n    pass\n")
    entrypoints = _find_python_entrypoints(str(tmp_path))
    assert entrypoints == ["app.py"]


def test_find_python_entrypoints_ignores_files_without_a_guard(tmp_path):
    (tmp_path / "lib.py").write_text("def helper():\n    pass\n")
    assert _find_python_entrypoints(str(tmp_path)) == []


def test_find_python_entrypoints_ignores_files_that_do_not_parse(tmp_path):
    (tmp_path / "broken.py").write_text("def helper(:\n    pass\n")
    assert _find_python_entrypoints(str(tmp_path)) == []


def test_find_python_entrypoints_empty_directory(tmp_path):
    assert _find_python_entrypoints(str(tmp_path)) == []


def test_entrypoint_ordering_spec_is_none_without_any_entrypoint(tmp_path):
    assert _entrypoint_ordering_spec(str(tmp_path), []) is None


def test_entrypoint_ordering_spec_passes_when_guard_is_last(tmp_path):
    (tmp_path / "app.py").write_text(_ORDERING_OK_SOURCE)

    spec = _entrypoint_ordering_spec(str(tmp_path), ["app.py"])
    outcome = execute_check(spec)

    assert outcome.status == "passed"


def test_entrypoint_ordering_spec_fails_when_something_is_defined_after_the_guard(tmp_path):
    # This is the exact live bug: `helper` (standing in for the real
    # project's `solve`) is defined after `if __name__ == "__main__":
    # main()`, and `main` calls it.
    (tmp_path / "app.py").write_text(_ORDERING_BUG_SOURCE)

    spec = _entrypoint_ordering_spec(str(tmp_path), ["app.py"])
    outcome = execute_check(spec)

    assert outcome.status == "failed"
    assert "app.py" in outcome.output
    assert "helper" in outcome.output
    assert "not the file's last top-level statement" in outcome.output


def test_python_plan_adds_entrypoint_checks_when_a_guard_is_present(tmp_path):
    (tmp_path / "app.py").write_text(_ORDERING_OK_SOURCE)

    plan = _python_plan(str(tmp_path))

    names = {s.name for s in plan}
    assert "entrypoint-ordering" in names
    assert "python app.py" in names
    entrypoint_check = next(s for s in plan if s.name == "python app.py")
    assert entrypoint_check.primary is False


def test_python_plan_has_no_entrypoint_checks_without_a_guard(tmp_path):
    # Preserves existing behavior for every pre-existing fixture in this
    # file (bare directories of test_*.py with no __main__ guard anywhere).
    (tmp_path / "test_fixture.py").write_text("def test_ok():\n    assert True\n")

    plan = _python_plan(str(tmp_path))

    assert {s.name for s in plan} == {"pytest"}


def test_full_verification_catches_the_live_ordering_bug_even_though_pytest_passes(tmp_path):
    # The exact shape of the live failure: a test suite that only imports
    # the module (and therefore never calls into the __main__ guard) passes
    # cleanly, while running the script directly would crash. Verification
    # must not report "verified" here.
    (tmp_path / "app.py").write_text(_ORDERING_BUG_SOURCE)
    (tmp_path / "test_app.py").write_text(
        "from app import main\n\ndef test_main_is_importable():\n    assert callable(main)\n"
    )

    run = run_full_verification(str(tmp_path))

    pytest_check = next(c for c in run.checks if c.name == "pytest")
    assert pytest_check.status == "passed"
    assert run.state == "failed"
    ordering_check = next(c for c in run.checks if c.name == "entrypoint-ordering")
    assert ordering_check.status == "failed"


def test_full_verification_passes_when_entrypoint_ordering_is_correct(tmp_path):
    (tmp_path / "app.py").write_text(_ORDERING_OK_SOURCE)
    (tmp_path / "test_app.py").write_text(
        "from app import main\n\ndef test_main_is_importable():\n    assert callable(main)\n"
    )

    run = run_full_verification(str(tmp_path))

    assert run.state == "verified"


def test_entrypoint_smoke_check_fails_on_a_real_startup_crash(tmp_path):
    # A script that crashes immediately on an uncaught exception — nothing
    # to do with the ordering bug, a plain startup-crash check.
    (tmp_path / "app.py").write_text("if __name__ == '__main__':\n    raise RuntimeError('boom')\n")

    plan = _python_plan(str(tmp_path))
    entrypoint_check = next(s for s in plan if s.name == "python app.py")
    outcome = execute_check(entrypoint_check)

    assert outcome.status == "failed"
    assert "boom" in outcome.output


# --- Stage 4: structured results / aggregation ------------------------------


def _check(
    name="pytest", command="pytest -q", *, primary=True, status="passed", exit_code=0, output=""
) -> CheckOutcome:
    return CheckOutcome(
        name=name,
        command=command,
        primary=primary,
        status=status,
        exit_code=exit_code,
        project_root="/tmp/x",
        summary="",
        output=output,
    )


def test_verification_run_is_verified_when_primary_passes():
    run = VerificationRun(checks=[_check(status="passed")])
    assert run.state == "verified"
    assert run.failing == []


def test_verification_run_is_failed_when_primary_fails():
    run = VerificationRun(checks=[_check(status="failed")])
    assert run.state == "failed"
    assert run.failing == run.checks


def test_verification_run_mixed_checks_one_passes_one_fails():
    # A configured lint/typecheck failure must block "verified" even though
    # the primary test/build check passed — never report success while a
    # configured check is genuinely failing.
    run = VerificationRun(
        checks=[
            _check(status="passed"),
            _check(name="ruff", command="ruff check .", primary=False, status="failed"),
        ]
    )
    assert run.state == "failed"
    assert [c.name for c in run.failing] == ["ruff"]


def test_verification_run_is_inconclusive_when_primary_skipped():
    run = VerificationRun(checks=[_check(status="skipped", exit_code=5)])
    assert run.state == "inconclusive"


def test_verification_run_is_inconclusive_when_primary_unavailable():
    run = VerificationRun(checks=[_check(status="unavailable", exit_code=None)])
    assert run.state == "inconclusive"


def test_verification_run_is_inconclusive_when_empty():
    assert VerificationRun(checks=[]).state == "inconclusive"


def test_verification_run_secondary_skip_does_not_block_verified():
    run = VerificationRun(
        checks=[
            _check(status="passed"),
            _check(name="mypy", command="mypy .", primary=False, status="unavailable"),
        ]
    )
    assert run.state == "verified"
    assert run.limitation_notes == ["mypy: "]


def test_verification_run_timed_out_is_distinct_from_failed():
    run = VerificationRun(checks=[_check(status="timed_out", exit_code=None)])
    assert run.state == "timed_out"
    assert run.timed_out_checks == run.checks
    assert run.failing == []


def test_verification_run_failed_takes_priority_over_timed_out():
    run = VerificationRun(
        checks=[
            _check(status="timed_out", exit_code=None),
            _check(name="ruff", primary=False, status="failed"),
        ]
    )
    assert run.state == "failed"


def test_verification_run_skipped_checks_are_not_limitations():
    run = VerificationRun(
        checks=[_check(status="passed"), _check(name="ruff", primary=False, status="skipped")]
    )
    assert run.limitation_notes == []


def test_verification_run_commands_omits_unavailable_empty_commands():
    run = VerificationRun(
        checks=[
            _check(command="pytest -q"),
            _check(name="mypy", command="", primary=False, status="unavailable"),
        ]
    )
    assert run.commands == ["pytest -q"]


# --- Full pipeline: run_full_verification -----------------------------------


def test_full_verification_python_project_with_passing_tests(tmp_path):
    (tmp_path / "test_fixture.py").write_text("def test_ok():\n    assert True\n")

    run = run_full_verification(str(tmp_path))

    assert run.state == "verified"
    assert [c.name for c in run.checks] == ["pytest"]


def test_full_verification_python_project_with_failing_tests(tmp_path):
    _write_fixture(tmp_path)

    run = run_full_verification(str(tmp_path))

    assert run.state == "failed"


def test_full_verification_no_test_python_project_is_inconclusive(tmp_path):
    # A recognized Python project (has actual .py source) with no tests
    # defined yet — inconclusive, not a failure, per the pytest exit-5
    # exemption.
    (tmp_path / "main.py").write_text("print('hello')\n")

    run = run_full_verification(str(tmp_path))

    assert run.state == "inconclusive"
    assert run.checks[0].status == "skipped"


def test_full_verification_unknown_project_is_inconclusive_and_unavailable(tmp_path):
    (tmp_path / "README.md").write_text("nothing recognizable here\n")

    run = run_full_verification(str(tmp_path))

    assert run.state == "inconclusive"
    assert run.checks[0].status == "unavailable"
    assert run.limitation_notes


def test_full_verification_monorepo_with_sibling_projects_is_inconclusive_and_unavailable(
    tmp_path,
):
    # A workspace containing two unrelated applications with no manifest of
    # its own — full pipeline must not silently verify just one of them.
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    _write_package_json(frontend, {"build": "vite build"})
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname = 'backend'\n")

    run = run_full_verification(str(tmp_path))

    assert run.state == "inconclusive"
    assert run.checks[0].status == "unavailable"
    assert "ambiguous" in run.limitation_notes[0]
    assert str(frontend) in run.limitation_notes[0]
    assert str(backend) in run.limitation_notes[0]


def test_full_verification_runs_ruff_when_configured_and_available(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / "test_fixture.py").write_text("def test_ok():\n    assert True\n")
    # An unused import is a real ruff violation (F401) — deterministic, no
    # network, doesn't depend on any project-specific ruff config beyond the
    # bare [tool.ruff] section above.
    (tmp_path / "bad.py").write_text("import os\n")

    run = run_full_verification(str(tmp_path))

    assert {c.name for c in run.checks} == {"pytest", "ruff"}
    assert run.state == "failed"
    ruff_check = next(c for c in run.checks if c.name == "ruff")
    assert ruff_check.status == "failed"


@_requires_npm
def test_full_verification_node_project_build_only(tmp_path):
    _write_package_json(tmp_path, {"build": 'node -e "process.exit(0)"'})

    run = run_full_verification(str(tmp_path))

    assert [c.name for c in run.checks] == ["npm run build"]
    assert run.state == "verified"


@_requires_npm
def test_full_verification_node_project_test_script_failure_fails_overall(tmp_path):
    _write_package_json(
        tmp_path,
        {"build": 'node -e "process.exit(0)"', "test": 'node -e "process.exit(1)"'},
    )

    run = run_full_verification(str(tmp_path))

    assert run.state == "failed"
    test_check = next(c for c in run.checks if c.name == "npm test")
    assert test_check.status == "failed"


@_requires_go
def test_full_verification_go_project_real_pass(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/fixture\n\ngo 1.21\n")
    (tmp_path / "fixture_test.go").write_text(
        'package fixture\n\nimport "testing"\n\nfunc TestOK(t *testing.T) {}\n'
    )

    run = run_full_verification(str(tmp_path))

    assert run.state == "verified"
    assert {c.name for c in run.checks} == {"go test", "go vet"}
    assert all(c.status == "passed" for c in run.checks)


@_requires_go
def test_full_verification_go_project_real_failure(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/fixture\n\ngo 1.21\n")
    (tmp_path / "fixture_test.go").write_text(
        'package fixture\n\nimport "testing"\n\nfunc TestFails(t *testing.T) { t.Fatal("boom") }\n'
    )

    run = run_full_verification(str(tmp_path))

    assert run.state == "failed"
    test_check = next(c for c in run.checks if c.name == "go test")
    assert test_check.status == "failed"
    assert "boom" in test_check.output


@_requires_cargo
def test_full_verification_rust_project_real_pass(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "fixture"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text(
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n"
        "#[cfg(test)]\nmod tests {\n"
        "    use super::*;\n"
        "    #[test]\n"
        "    fn it_adds() { assert_eq!(add(2, 2), 4); }\n"
        "}\n"
    )

    run = run_full_verification(str(tmp_path))

    assert run.state == "verified"
    assert {c.name for c in run.checks} == {"cargo test", "cargo check", "cargo clippy"}


def test_full_verification_java_maven_project_reports_unavailable(tmp_path):
    # mvn/mvnw genuinely aren't installed in this dev environment — a real
    # exercise of the "configured but no toolchain" path, same pattern as
    # the mypy test above.
    (tmp_path / "pom.xml").write_text("<project></project>\n")

    run = run_full_verification(str(tmp_path))

    assert shutil.which("mvn") is None  # confirms the test exercises the real gap
    assert run.state == "inconclusive"
    assert run.checks[0].status == "unavailable"


def test_full_verification_java_maven_with_fake_wrapper_can_pass(tmp_path):
    # Exercises the "verified" path for Java without needing a real Maven
    # installation: a wrapper script that always exits 0 stands in for it.
    wrapper = tmp_path / "mvnw"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    (tmp_path / "pom.xml").write_text("<project></project>\n")

    run = run_full_verification(str(tmp_path))

    assert run.state == "verified"
    assert run.checks[0].name == "mvn test"


# --- Legacy detection helpers used by plan discovery ------------------------


def test_ruff_not_configured_by_default(tmp_path):
    assert _ruff_configured(str(tmp_path)) is False


def test_ruff_configured_via_pyproject_tool_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    assert _ruff_configured(str(tmp_path)) is True


def test_ruff_configured_via_standalone_toml_file(tmp_path):
    (tmp_path / "ruff.toml").write_text("line-length = 100\n")
    assert _ruff_configured(str(tmp_path)) is True


def test_mypy_not_configured_by_default(tmp_path):
    assert _mypy_configured(str(tmp_path)) is False


def test_mypy_configured_via_pyproject_tool_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    assert _mypy_configured(str(tmp_path)) is True


def test_mypy_configured_via_ini_file(tmp_path):
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = True\n")
    assert _mypy_configured(str(tmp_path)) is True


def test_malformed_pyproject_toml_does_not_crash_detection(tmp_path):
    (tmp_path / "pyproject.toml").write_text("this is not [ valid toml")
    assert _ruff_configured(str(tmp_path)) is False
    assert _mypy_configured(str(tmp_path)) is False


def test_npm_test_script_returns_a_real_script(tmp_path):
    _write_package_json(tmp_path, {"test": "vitest run"})
    assert _npm_test_script(str(tmp_path)) == "vitest run"


def test_npm_test_script_ignores_the_default_placeholder(tmp_path):
    _write_package_json(tmp_path, {"test": 'echo "Error: no test specified" && exit 1'})
    assert _npm_test_script(str(tmp_path)) is None


def test_npm_test_script_is_none_when_undefined(tmp_path):
    _write_package_json(tmp_path, {"build": "vite build"})
    assert _npm_test_script(str(tmp_path)) is None


def test_npm_test_script_is_none_without_package_json(tmp_path):
    assert _npm_test_script(str(tmp_path)) is None
