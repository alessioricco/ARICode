"""Tests for the shared skill catalog loader and per-project context writer.
No LLM, no network — just filesystem fixtures.
"""

from __future__ import annotations

from harness.skills import load_skill_catalog, write_project_context

_KEYWORD_SKILL = """---
name: sample-keyword-skill
triggers:
  - widget
description: A sample keyword-triggered skill.
---

Widgets must be blue.
"""

_PATH_SKILL = """---
name: sample-path-rule
paths:
  - "*.toml"
description: A sample path-triggered rule.
---

Watch your TOML.
"""

_REPO_SKILL = """---
name: sample-repo-skill
description: Always-active, no trigger.
---

This project always does X.
"""


def test_returns_empty_list_for_missing_directory(tmp_path):
    assert load_skill_catalog(tmp_path / "does-not-exist") == []


def test_loads_skills_from_nested_subfolders(tmp_path):
    (tmp_path / "category-a").mkdir()
    (tmp_path / "category-a" / "keyword.md").write_text(_KEYWORD_SKILL)
    (tmp_path / "category-b" / "nested").mkdir(parents=True)
    (tmp_path / "category-b" / "nested" / "path.md").write_text(_PATH_SKILL)
    (tmp_path / "repo.md").write_text(_REPO_SKILL)

    skills = {s.name: s for s in load_skill_catalog(tmp_path)}

    assert set(skills) == {"sample-keyword-skill", "sample-path-rule", "sample-repo-skill"}
    assert skills["sample-keyword-skill"].get_skill_type() == "knowledge"
    assert skills["sample-keyword-skill"].match_trigger("I need a widget") == "widget"
    assert skills["sample-path-rule"].match_path_trigger("config.toml") == "*.toml"
    assert skills["sample-repo-skill"].get_skill_type() == "repo"


def test_write_project_context_creates_agents_md(tmp_path):
    write_project_context(str(tmp_path), "This project uses FastAPI + Poetry.")

    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.read_text() == "This project uses FastAPI + Poetry."


def test_write_project_context_overwrites_existing(tmp_path):
    (tmp_path / "AGENTS.md").write_text("old content")

    write_project_context(str(tmp_path), "new content")

    assert (tmp_path / "AGENTS.md").read_text() == "new content"
