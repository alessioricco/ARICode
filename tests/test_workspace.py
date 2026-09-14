"""Unit tests for build_workspace. The docker branch never touches a real
Docker daemon: subprocess and DockerWorkspace are monkeypatched.
"""

from __future__ import annotations

import subprocess

from harness import workspace as workspace_mod
from harness.config import Config


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


def test_local_execution_yields_plain_workspace_string():
    cfg = _cfg(execution="local", workspace="/some/dir")

    with workspace_mod.build_workspace(cfg) as ws:
        assert ws == "/some/dir"


def test_docker_execution_builds_image_when_missing(monkeypatch, tmp_path):
    build_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(cmd, returncode=1)
        if cmd[:2] == ["docker", "build"]:
            build_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(workspace_mod.subprocess, "run", fake_run)

    created = {}

    class FakeDockerWorkspace:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("openhands.workspace.DockerWorkspace", FakeDockerWorkspace)

    project_dir = tmp_path / "myproject"
    cfg = _cfg(execution="docker", workspace=str(project_dir), docker_image="test/image:local")

    result = workspace_mod.build_workspace(cfg)

    assert isinstance(result, FakeDockerWorkspace)
    assert project_dir.is_dir()
    assert created["server_image"] == "test/image:local"
    assert created["volumes"] == [f"{project_dir}:/workspace"]
    assert build_calls, "expected docker build to run when the image is missing"


def test_docker_execution_skips_build_when_image_present(monkeypatch, tmp_path):
    build_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "build"]:
            build_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(workspace_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("openhands.workspace.DockerWorkspace", lambda **kwargs: kwargs)

    cfg = _cfg(execution="docker", workspace=str(tmp_path / "p"))
    workspace_mod.build_workspace(cfg)

    assert build_calls == []
