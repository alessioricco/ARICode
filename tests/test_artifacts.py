"""Tests for artifacts.py: pure path resolution + JSON file writing. No SDK,
no network, no real Conversation.
"""

from __future__ import annotations

import json

import pytest

from harness.acceptance import AcceptanceCheck, AcceptanceCheckResult
from harness.artifacts import resolve_run_artifacts_dir, write_run_artifacts
from harness.config import ConfigError
from harness.runner import CompletionContract, TaskOutcome

_CONTRACT = CompletionContract(
    goal="do the thing",
    acceptance_criteria=["it works"],
    verification_checks=["pytest -q"],
    limitations=[],
)


def _outcome(verification_state="verified", acceptance_results=()) -> TaskOutcome:
    return TaskOutcome(
        verification_state=verification_state,
        completion_contract=_CONTRACT,
        acceptance_results=tuple(acceptance_results),
    )


# --- resolve_run_artifacts_dir -----------------------------------------


def test_resolve_run_artifacts_dir_with_a_project(tmp_path):
    result = resolve_run_artifacts_dir(str(tmp_path), "my-project", "run-1")

    assert result == str(tmp_path / "my-project" / "run-1")


def test_resolve_run_artifacts_dir_with_no_project_uses_unscoped_bucket(tmp_path):
    result = resolve_run_artifacts_dir(str(tmp_path), None, "run-1")

    assert result == str(tmp_path / "_unscoped" / "run-1")


def test_resolve_run_artifacts_dir_rejects_absolute_project(tmp_path):
    with pytest.raises(ConfigError, match="must be relative"):
        resolve_run_artifacts_dir(str(tmp_path), "/etc/cron.d", "run-1")


def test_resolve_run_artifacts_dir_rejects_dotdot_project(tmp_path):
    with pytest.raises(ConfigError, match="must not contain"):
        resolve_run_artifacts_dir(str(tmp_path), "../escape", "run-1")


def test_resolve_run_artifacts_dir_rejects_dot_and_dotdot_literal(tmp_path):
    with pytest.raises(ConfigError, match="Invalid project name"):
        resolve_run_artifacts_dir(str(tmp_path), "..", "run-1")


def test_resolve_run_artifacts_dir_rejects_a_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "escaped").symlink_to(outside)

    with pytest.raises(ConfigError, match="escapes HARNESS_ARTIFACTS_DIR"):
        resolve_run_artifacts_dir(str(root), "escaped", "run-1")


def test_resolve_run_artifacts_dir_allows_a_symlink_that_stays_inside(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    real = root / "real-project"
    real.mkdir()
    (root / "aliased").symlink_to(real)

    result = resolve_run_artifacts_dir(str(root), "aliased", "run-1")

    assert result == str(root / "aliased" / "run-1")


# --- write_run_artifacts -----------------------------------------------


def _write(tmp_path, **overrides):
    kwargs = dict(
        artifacts_dir=str(tmp_path),
        run_id="run-1",
        project="my-project",
        task="do the thing",
        execution="local",
        model="anthropic/claude-sonnet-4-5-20250929",
        model_selection="manual",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:01:00+00:00",
        messages=[{"role": "user", "content": [{"text": "do the thing"}]}],
        outcome=_outcome(),
        error=None,
        combined_metrics={"accumulated_cost": 0.01},
        per_model_metrics={"harness": {"accumulated_cost": 0.01}},
    )
    kwargs.update(overrides)
    write_run_artifacts(**kwargs)
    return tmp_path / "my-project" / "run-1"


def test_write_run_artifacts_creates_all_three_files(tmp_path):
    run_dir = _write(tmp_path)

    assert (run_dir / "metadata.json").exists()
    assert (run_dir / "transcript.json").exists()
    assert (run_dir / "metrics.json").exists()


def test_metadata_json_has_expected_fields(tmp_path):
    run_dir = _write(tmp_path)

    metadata = json.loads((run_dir / "metadata.json").read_text())

    assert metadata["run_id"] == "run-1"
    assert metadata["project"] == "my-project"
    assert metadata["task"] == "do the thing"
    assert metadata["execution"] == "local"
    assert metadata["model"] == "anthropic/claude-sonnet-4-5-20250929"
    assert metadata["model_selection"] == "manual"
    assert metadata["verification_state"] == "verified"
    assert metadata["completion_contract"]["goal"] == "do the thing"
    assert metadata["error"] is None


def test_metadata_json_handles_a_none_outcome(tmp_path):
    run_dir = _write(tmp_path, outcome=None, error="RuntimeError: boom")

    metadata = json.loads((run_dir / "metadata.json").read_text())

    assert metadata["verification_state"] is None
    assert metadata["completion_contract"] is None
    assert metadata["acceptance_results"] is None
    assert metadata["error"] == "RuntimeError: boom"


def test_metadata_json_includes_acceptance_results_when_present(tmp_path):
    check = AcceptanceCheck(kind="file_exists", path="OUTPUT.txt")
    result = AcceptanceCheckResult(check=check, passed=True, detail="OUTPUT.txt exists")
    outcome = _outcome(acceptance_results=[result])
    run_dir = _write(tmp_path, outcome=outcome)

    metadata = json.loads((run_dir / "metadata.json").read_text())

    assert metadata["acceptance_results"][0]["passed"] is True
    assert metadata["acceptance_results"][0]["detail"] == "OUTPUT.txt exists"


def test_transcript_json_matches_the_given_messages(tmp_path):
    messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]
    run_dir = _write(tmp_path, messages=messages)

    assert json.loads((run_dir / "transcript.json").read_text()) == messages


def test_metrics_json_has_combined_and_per_model(tmp_path):
    run_dir = _write(
        tmp_path,
        combined_metrics={"accumulated_cost": 0.05},
        per_model_metrics={
            "harness:a": {"accumulated_cost": 0.02},
            "harness:b": {"accumulated_cost": 0.03},
        },
    )

    metrics = json.loads((run_dir / "metrics.json").read_text())

    assert metrics["combined"] == {"accumulated_cost": 0.05}
    assert metrics["per_model"] == {
        "harness:a": {"accumulated_cost": 0.02},
        "harness:b": {"accumulated_cost": 0.03},
    }


def test_write_run_artifacts_with_no_project_uses_unscoped_bucket(tmp_path):
    write_run_artifacts(
        artifacts_dir=str(tmp_path),
        run_id="run-2",
        project=None,
        task="t",
        execution="local",
        model="openai/gpt-4o",
        model_selection="manual",
        started_at="s",
        ended_at="e",
        messages=[],
        outcome=None,
        error=None,
        combined_metrics={},
        per_model_metrics={},
    )

    assert (tmp_path / "_unscoped" / "run-2" / "metadata.json").exists()
