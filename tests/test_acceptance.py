"""Tests for harness.acceptance — pure filesystem checks, no SDK, no network."""

from __future__ import annotations

import pytest

from harness.acceptance import (
    AcceptanceCheck,
    AcceptanceCheckError,
    evaluate_acceptance_check,
    evaluate_acceptance_checks,
    parse_acceptance_check,
    parse_acceptance_checks,
)

# --- parse_acceptance_check: input validation -------------------------------


def test_parse_file_exists_check():
    check = parse_acceptance_check({"kind": "file_exists", "path": "README.md"})
    assert check == AcceptanceCheck(kind="file_exists", path="README.md")


def test_parse_file_contains_check():
    check = parse_acceptance_check(
        {"kind": "file_contains", "path": "README.md", "contains": "Usage"}
    )
    assert check.kind == "file_contains"
    assert check.contains == "Usage"


def test_parse_defaults_required_to_true():
    check = parse_acceptance_check({"kind": "file_exists", "path": "x.txt"})
    assert check.required is True


def test_parse_honors_required_false():
    check = parse_acceptance_check({"kind": "file_exists", "path": "x.txt", "required": False})
    assert check.required is False


def test_parse_honors_description():
    check = parse_acceptance_check(
        {"kind": "file_exists", "path": "x.txt", "description": "the output file"}
    )
    assert check.description == "the output file"


def test_parse_rejects_a_non_object_item():
    with pytest.raises(AcceptanceCheckError, match="must be a JSON object"):
        parse_acceptance_check("not a dict")


def test_parse_rejects_unknown_kind():
    with pytest.raises(AcceptanceCheckError, match="Unknown acceptance check kind"):
        parse_acceptance_check({"kind": "run_command", "path": "x"})


@pytest.mark.parametrize("bad_path", [None, "", "   ", 123])
def test_parse_rejects_missing_or_invalid_path(bad_path):
    with pytest.raises(AcceptanceCheckError, match="'path'"):
        parse_acceptance_check({"kind": "file_exists", "path": bad_path})


def test_parse_file_contains_requires_contains():
    with pytest.raises(AcceptanceCheckError, match="requires a non-empty 'contains'"):
        parse_acceptance_check({"kind": "file_contains", "path": "x.txt"})


def test_parse_file_exists_rejects_a_contains_field():
    with pytest.raises(AcceptanceCheckError, match="'contains' is not valid"):
        parse_acceptance_check({"kind": "file_exists", "path": "x.txt", "contains": "y"})


def test_parse_rejects_non_bool_required():
    with pytest.raises(AcceptanceCheckError, match="'required' must be a boolean"):
        parse_acceptance_check({"kind": "file_exists", "path": "x.txt", "required": "yes"})


def test_parse_rejects_non_string_description():
    with pytest.raises(AcceptanceCheckError, match="'description' must be a string"):
        parse_acceptance_check({"kind": "file_exists", "path": "x.txt", "description": 5})


def test_parse_acceptance_checks_parses_a_list():
    checks = parse_acceptance_checks(
        [{"kind": "file_exists", "path": "a.txt"}, {"kind": "file_exists", "path": "b.txt"}]
    )
    assert [c.path for c in checks] == ["a.txt", "b.txt"]


# --- evaluate_acceptance_check: file_exists ---------------------------------


def test_file_exists_passes_when_file_present(tmp_path):
    (tmp_path / "README.md").write_text("hello")
    check = AcceptanceCheck(kind="file_exists", path="README.md")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is True
    assert "exists" in result.detail


def test_file_exists_fails_when_file_absent(tmp_path):
    check = AcceptanceCheck(kind="file_exists", path="missing.txt")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is False
    assert "does not exist" in result.detail


def test_file_exists_allows_a_nested_relative_path(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    check = AcceptanceCheck(kind="file_exists", path="src/main.py")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is True


# --- evaluate_acceptance_check: file_contains -------------------------------


def test_file_contains_passes_when_text_present(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\n\n## Usage\n\nRun it.")
    check = AcceptanceCheck(kind="file_contains", path="README.md", contains="Usage")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is True


def test_file_contains_fails_when_text_absent(tmp_path):
    (tmp_path / "README.md").write_text("# My Project")
    check = AcceptanceCheck(kind="file_contains", path="README.md", contains="Usage")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is False
    assert "does not contain" in result.detail


def test_file_contains_fails_when_file_absent(tmp_path):
    check = AcceptanceCheck(kind="file_contains", path="missing.txt", contains="x")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is False
    assert "does not exist" in result.detail


def test_file_contains_caps_read_size_and_notes_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.acceptance._MAX_FILE_READ_BYTES", 10)
    (tmp_path / "big.txt").write_text("a" * 5 + "NEEDLE" + "b" * 20)
    check = AcceptanceCheck(kind="file_contains", path="big.txt", contains="NEEDLE")

    result = evaluate_acceptance_check(str(tmp_path), check)

    # "NEEDLE" starts at byte 5 and the cap is 10 bytes, so it's only
    # partially within the read window — a real, not contrived, miss.
    assert result.passed is False
    assert "checked only the first 1MB" in result.detail


# --- path containment: the actual security property this module exists for -


def test_rejects_an_absolute_path(tmp_path):
    check = AcceptanceCheck(kind="file_exists", path="/etc/passwd")

    result = evaluate_acceptance_check(str(tmp_path), check)

    assert result.passed is False
    assert "must be relative, not absolute" in result.detail


def test_rejects_dotdot_traversal(tmp_path):
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("root:x:0:0")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    check = AcceptanceCheck(kind="file_contains", path="../outside_secret.txt", contains="root")

    result = evaluate_acceptance_check(str(workspace), check)

    assert result.passed is False
    assert "must not contain '..'" in result.detail
    outside.unlink()


def test_rejects_a_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    check = AcceptanceCheck(kind="file_contains", path="link/secret.txt", contains="secret")

    result = evaluate_acceptance_check(str(workspace), check)

    assert result.passed is False
    assert "escapes the task workspace" in result.detail


def test_allows_a_symlink_that_stays_inside(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_dir = workspace / "real"
    real_dir.mkdir()
    (real_dir / "out.txt").write_text("done")
    (workspace / "link").symlink_to(real_dir, target_is_directory=True)
    check = AcceptanceCheck(kind="file_exists", path="link/out.txt")

    result = evaluate_acceptance_check(str(workspace), check)

    assert result.passed is True


# --- evaluate_acceptance_checks: batch --------------------------------------


def test_evaluate_acceptance_checks_evaluates_each_independently(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    checks = [
        AcceptanceCheck(kind="file_exists", path="a.txt"),
        AcceptanceCheck(kind="file_exists", path="b.txt"),
    ]

    results = evaluate_acceptance_checks(str(tmp_path), checks)

    assert [r.passed for r in results] == [True, False]
