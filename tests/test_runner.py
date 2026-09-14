"""End-to-end smoke test for the runner.

Skips cleanly when no LLM key is configured, so CI passes without secrets. When
a key is present, it runs one tiny, cheap task against whichever provider/model
`.env` currently points at (proving the harness is provider-agnostic: this test
never hardcodes a model).
"""

from __future__ import annotations

import pytest

from harness.config import ConfigError, load_config


def _config_available() -> bool:
    try:
        load_config()
    except ConfigError:
        return False
    return True


@pytest.mark.skipif(not _config_available(), reason="No LLM_MODEL/LLM_API_KEY configured in .env")
def test_run_task_creates_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    from harness.runner import run_task

    target = tmp_path / "HELLO.txt"
    messages = run_task(
        f"Create a file at the absolute path {target} containing the single "
        "line: hello. Then finish."
    )

    assert messages, "expected at least one captured message"
    assert target.exists()
    assert "hello" in target.read_text().lower()
