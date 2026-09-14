"""Builds the workspace the agent operates in.

Single dispatch point for execution backends: HARNESS_EXECUTION selects a
branch here. Adding a future backend (ECS, EC2, ...) means adding one branch
in this module and one value to config.EXECUTION_MODES — not touching
agent.py, runner.py, or cli.py.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import nullcontext
from typing import Any

from .config import Config

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOCKERFILE = os.path.join(_REPO_ROOT, "docker", "agent-server.Dockerfile")


def _ensure_docker_image(image: str) -> None:
    check = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if check.returncode == 0:
        return
    print(f"Building agent-server image '{image}' (first run only)...")
    subprocess.run(["docker", "build", "-f", _DOCKERFILE, "-t", image, _REPO_ROOT], check=True)


def build_workspace(cfg: Config) -> Any:
    """Return a context manager yielding the workspace value for `Conversation`.

    Local: a plain string, wrapped in `nullcontext` (nothing to clean up).
    Docker: a `DockerWorkspace`, itself a working context manager that starts
    the container on construction and stops it on `__exit__`.
    """
    if cfg.execution == "local":
        return nullcontext(cfg.workspace)

    if cfg.execution == "docker":
        try:
            from openhands.workspace import DockerWorkspace
        except ImportError as exc:
            raise RuntimeError(
                "HARNESS_EXECUTION=docker requires the 'sandbox' extra: "
                'run `uv pip install -e ".[sandbox]"`.'
            ) from exc

        _ensure_docker_image(cfg.docker_image)
        host_dir = os.path.abspath(cfg.workspace)
        os.makedirs(host_dir, exist_ok=True)
        return DockerWorkspace(
            server_image=cfg.docker_image,
            platform=cfg.docker_platform,
            volumes=[f"{host_dir}:/workspace"],
        )

    raise ValueError(f"Unsupported HARNESS_EXECUTION: {cfg.execution!r}")
