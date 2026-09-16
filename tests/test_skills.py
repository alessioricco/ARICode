"""Tests for the shared skill catalog loader and per-project context writer.
No LLM, no network — just filesystem fixtures, plus a set of tests against
this repo's own real `skills/` catalog (including the lifecycle skills under
`skills/lifecycle/`) to confirm real triggering behavior, not just the
loader mechanics against synthetic fixtures.
"""

from __future__ import annotations

from pathlib import Path

from harness.skills import load_skill_catalog, write_project_context

_REAL_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

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


# --- The real skills/ catalog, including skills/lifecycle/ -----------------


def _real_catalog() -> dict:
    return {s.name: s for s in load_skill_catalog(_REAL_SKILLS_DIR)}


_LIFECYCLE_SKILL_NAMES = (
    "repository-discovery",
    "requirements-analysis",
    "implementation-planning",
    "testing-and-verification",
    "debugging-and-failure-repair",
    "security-review",
    "documentation-and-operational-readiness",
    "completion-and-release-readiness",
)


def test_every_lifecycle_skill_loads():
    catalog = _real_catalog()

    for name in _LIFECYCLE_SKILL_NAMES:
        assert name in catalog, f"{name} did not load from skills/lifecycle/"
        assert (
            catalog[name].get_skill_type() == "knowledge"
        )  # legacy KeywordTrigger, not AgentSkills


def test_nested_skill_directories_remain_supported():
    # skills/lifecycle/ is itself a new nested subfolder, loaded alongside
    # the pre-existing skills/git/, skills/python-web/, skills/testing/ —
    # all four must be present together, confirming rglob-based nested
    # loading wasn't broken by adding a new category.
    catalog = _real_catalog()

    assert "commit-conventions" in catalog  # skills/git/
    assert "fastapi-conventions" in catalog  # skills/python-web/
    assert "pin-dependencies" in catalog  # skills/python-web/
    assert "pytest-conventions" in catalog  # skills/testing/
    for name in _LIFECYCLE_SKILL_NAMES:  # skills/lifecycle/
        assert name in catalog


def test_preexisting_agentskills_directories_are_unchanged():
    # frontend-design/webapp-testing/web-artifacts-builder (sourced from
    # anthropics/skills) must survive this change untouched.
    catalog = _real_catalog()

    for name in ("frontend-design", "webapp-testing", "web-artifacts-builder"):
        assert catalog[name].get_skill_type() == "agentskills"


def test_preexisting_legacy_skill_triggers_are_unchanged():
    catalog = _real_catalog()

    assert catalog["commit-conventions"].match_trigger("Open a pull request") == "pull request"
    assert catalog["fastapi-conventions"].match_trigger("Scaffold a FastAPI service") is not None
    assert catalog["pytest-conventions"].match_trigger("Add a pytest test") is not None
    assert catalog["pin-dependencies"].match_path_trigger("pyproject.toml") is not None


def test_no_skill_name_collisions_between_lifecycle_and_preexisting_skills():
    skills = load_skill_catalog(_REAL_SKILLS_DIR)
    names = [s.name for s in skills]

    assert len(names) == len(set(names)), f"duplicate skill names: {names}"


# --- Representative keyword triggers for each lifecycle skill --------------


def test_repository_discovery_triggers_on_a_substantial_coding_task():
    skill = _real_catalog()["repository-discovery"]
    assert skill.match_trigger("Implement a CSV export endpoint") == "implement"


def test_requirements_analysis_triggers_on_a_feature_request():
    skill = _real_catalog()["requirements-analysis"]
    assert skill.match_trigger("Add a feature for bulk delete") == "feature"


def test_implementation_planning_triggers_on_architectural_language():
    skill = _real_catalog()["implementation-planning"]
    assert skill.match_trigger("Refactor the auth module architecture") is not None


def test_testing_and_verification_triggers_on_a_bug_fix():
    skill = _real_catalog()["testing-and-verification"]
    assert skill.match_trigger("Fix the off-by-one bug in pagination") is not None


def test_debugging_triggers_on_a_failure_report():
    skill = _real_catalog()["debugging-and-failure-repair"]
    assert skill.match_trigger("The build is failing with a traceback") is not None


def test_security_review_triggers_on_auth_and_secrets_language():
    skill = _real_catalog()["security-review"]
    assert skill.match_trigger("Add password reset via email token") is not None
    assert skill.match_trigger("Store the API key in the database") is not None


def test_documentation_readiness_triggers_on_config_and_api_changes():
    skill = _real_catalog()["documentation-and-operational-readiness"]
    assert skill.match_trigger("Add a new config flag for the CLI") is not None


def test_completion_readiness_triggers_on_release_and_deploy_language():
    skill = _real_catalog()["completion-and-release-readiness"]
    assert skill.match_trigger("Prepare this for production deploy") is not None
    assert skill.match_trigger("Open a pull request") is not None


# --- Unrelated tasks must not activate overly broad skills ------------------


def test_pure_qa_task_does_not_trigger_lifecycle_skills():
    # A question with no coding-task language shouldn't fire skills meant
    # for actual implementation/verification/security work.
    catalog = _real_catalog()
    text = "What does the load_skill_catalog function do?"

    for name in (
        "requirements-analysis",
        "testing-and-verification",
        "debugging-and-failure-repair",
        "security-review",
        "completion-and-release-readiness",
    ):
        assert catalog[name].match_trigger(text) is None, f"{name} should not fire on {text!r}"


def test_unrelated_creative_task_does_not_trigger_any_lifecycle_skill():
    catalog = _real_catalog()
    text = "Write a haiku about autumn leaves"

    for name in _LIFECYCLE_SKILL_NAMES:
        assert catalog[name].match_trigger(text) is None, f"{name} should not fire on {text!r}"


def test_security_review_does_not_fire_on_unrelated_refactor():
    # "refactor" alone (implementation-planning's trigger) must not also
    # pull in security-review, which has its own distinct keyword set.
    skill = _real_catalog()["security-review"]
    assert skill.match_trigger("Refactor the formatting helper functions") is None
