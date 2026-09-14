"""Shared skill catalog + per-project context writing.

Two distinct mechanisms, both documented in MANUAL.md under "Skills":

- `load_skill_catalog()`: a shared library of reusable, trigger-based skills
  (this repo's `skills/`, organized into subfolders purely for human
  classification — see below for why that needs no special code). Loaded into
  every agent's `AgentContext`; the SDK matches each skill's own trigger
  against the task/conversation automatically, so no custom "which skill
  applies" logic lives here.
- `write_project_context()`: an explicit, caller-supplied AGENTS.md written
  into a specific project's workspace, for persistent per-project facts (not
  reusable expertise) — the harness writes it, not a human hand-editing files.
"""

from __future__ import annotations

import os
from pathlib import Path

from openhands.sdk.skills import Skill, load_skills_from_dir


def load_skill_catalog(root: str | Path) -> list[Skill]:
    """Load every skill under `root`, at any nesting depth.

    Subfolders (e.g. `skills/testing/`, `skills/web/`) are purely
    organizational: `load_skills_from_dir` finds legacy-format `.md` files
    recursively (`rglob("*.md")` under the hood), so no custom directory
    walk is needed here. AgentSkills-format `SKILL.md` directories are only
    detected one level deep (an SDK constraint, not ours) — nest those at the
    top of `root` if used, or prefer the flat `.md`-with-frontmatter format
    for anything in a category subfolder.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(root)
    return list({**repo_skills, **knowledge_skills, **agent_skills}.values())


def write_project_context(project_dir: str, content: str) -> None:
    """Write `content` as this project's AGENTS.md.

    The SDK's `load_project_skills()` already recognizes AGENTS.md as a
    third-party instruction file and loads it as an always-active skill with
    no frontmatter required — writing here is enough to make it apply to this
    run and every future task against the same project directory.
    """
    with open(os.path.join(project_dir, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write(content)
