"""Tests for model_catalog.py: pure parsing/classification/scoring, no SDK,
no network. Every YAML fixture is written to tmp_path and parsed for real —
no mocking of yaml.safe_load itself.
"""

from __future__ import annotations

import pytest

from harness.model_catalog import (
    ModelCatalog,
    ModelCatalogEntry,
    ModelCatalogError,
    TaskProfile,
    classify_task,
    load_model_catalog,
    rank_candidates,
    score_entry,
)

_MINIMAL_YAML = """
models:
  - name: only-one
    model: openai/gpt-4o-mini
    ratings: {reasoning: 2, cost: 5}
"""

_FULL_YAML = """
models:
  - name: fast-cheap
    model: openai/gpt-4o-mini
    api_key: sk-fake
    description: "Fast and cheap."
    ratings: {reasoning: 2, cost: 5, precision: 3, code: 3}
  - name: balanced
    model: anthropic/claude-sonnet-4-5-20250929
    ratings: {reasoning: 4, cost: 3, precision: 4, code: 4}
  - name: deep-reasoner
    model: anthropic/claude-opus-4
    base_url: "https://example.invalid"
    reasoning_effort: high
    ratings: {reasoning: 5, cost: 1, precision: 5, code: 5}

task_profiles:
  debugging:
    triggers: [fix, bug, crash]
    weights: {reasoning: 3, precision: 2, code: 1, cost: 0.5}
  scaffolding:
    triggers: [scaffold, "create a new"]
    weights: {cost: 2, code: 1}
  default:
    weights: {reasoning: 1, cost: 1, precision: 1, code: 1}
"""


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "models.yaml"
    path.write_text(text)
    return str(path)


# --- load_model_catalog: happy paths -----------------------------------


def test_loads_minimal_catalog_with_one_model_and_no_profiles(tmp_path):
    catalog = load_model_catalog(_write(tmp_path, _MINIMAL_YAML))

    assert len(catalog.models) == 1
    assert catalog.models[0] == ModelCatalogEntry(
        name="only-one",
        model="openai/gpt-4o-mini",
        ratings={"reasoning": 2.0, "cost": 5.0},
    )
    assert catalog.task_profiles == []


def test_loads_full_catalog_with_profiles(tmp_path):
    catalog = load_model_catalog(_write(tmp_path, _FULL_YAML))

    assert [m.name for m in catalog.models] == ["fast-cheap", "balanced", "deep-reasoner"]
    assert catalog.models[0].api_key == "sk-fake"
    assert catalog.models[2].base_url == "https://example.invalid"
    assert catalog.models[2].reasoning_effort == "high"
    assert {p.name for p in catalog.task_profiles} == {"debugging", "scaffolding", "default"}


# --- activated ---------------------------------------------------------


def test_activated_defaults_to_true_when_omitted(tmp_path):
    catalog = load_model_catalog(_write(tmp_path, _MINIMAL_YAML))

    assert catalog.models[0].activated is True


def test_activated_false_is_parsed(tmp_path):
    path = _write(
        tmp_path,
        "models:\n  - name: foo\n    model: openai/gpt-4o\n    activated: false\n",
    )
    catalog = load_model_catalog(path)

    assert catalog.models[0].activated is False


def test_activated_true_is_parsed(tmp_path):
    path = _write(
        tmp_path,
        "models:\n  - name: foo\n    model: openai/gpt-4o\n    activated: true\n",
    )
    catalog = load_model_catalog(path)

    assert catalog.models[0].activated is True


def test_non_boolean_activated_raises(tmp_path):
    path = _write(
        tmp_path,
        "models:\n  - name: foo\n    model: openai/gpt-4o\n    activated: maybe\n",
    )
    with pytest.raises(ModelCatalogError, match="'activated'"):
        load_model_catalog(path)


def test_duplicate_name_still_raises_when_one_is_deactivated(tmp_path):
    path = _write(
        tmp_path,
        "models:\n"
        "  - name: dup\n    model: openai/gpt-4o\n    activated: false\n"
        "  - name: dup\n    model: anthropic/claude-sonnet-4-5-20250929\n",
    )
    with pytest.raises(ModelCatalogError, match="duplicate model name"):
        load_model_catalog(path)


# --- load_model_catalog: error paths -------------------------------------


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(ModelCatalogError, match="no such file"):
        load_model_catalog(str(tmp_path / "does_not_exist.yaml"))


def test_invalid_yaml_raises(tmp_path):
    path = _write(tmp_path, "models: [this is not: valid: yaml: at all")
    with pytest.raises(ModelCatalogError, match="not valid YAML"):
        load_model_catalog(path)


def test_non_mapping_top_level_raises(tmp_path):
    path = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ModelCatalogError, match="mapping"):
        load_model_catalog(path)


def test_missing_models_key_raises(tmp_path):
    path = _write(tmp_path, "task_profiles: {}\n")
    with pytest.raises(ModelCatalogError, match="non-empty 'models' list"):
        load_model_catalog(path)


def test_empty_models_list_raises(tmp_path):
    path = _write(tmp_path, "models: []\n")
    with pytest.raises(ModelCatalogError, match="non-empty 'models' list"):
        load_model_catalog(path)


def test_model_entry_missing_name_raises(tmp_path):
    path = _write(tmp_path, "models:\n  - model: openai/gpt-4o\n")
    with pytest.raises(ModelCatalogError, match="'name'"):
        load_model_catalog(path)


def test_model_entry_missing_model_id_raises(tmp_path):
    path = _write(tmp_path, "models:\n  - name: foo\n")
    with pytest.raises(ModelCatalogError, match="'model'"):
        load_model_catalog(path)


def test_duplicate_model_names_raise(tmp_path):
    path = _write(
        tmp_path,
        "models:\n"
        "  - name: dup\n    model: openai/gpt-4o\n"
        "  - name: dup\n    model: anthropic/claude-sonnet-4-5-20250929\n",
    )
    with pytest.raises(ModelCatalogError, match="duplicate model name"):
        load_model_catalog(path)


def test_non_numeric_rating_raises(tmp_path):
    path = _write(
        tmp_path, "models:\n  - name: foo\n    model: openai/gpt-4o\n    ratings: {cost: high}\n"
    )
    with pytest.raises(ModelCatalogError, match="rating"):
        load_model_catalog(path)


def test_ratings_not_a_mapping_raises(tmp_path):
    path = _write(
        tmp_path, "models:\n  - name: foo\n    model: openai/gpt-4o\n    ratings: [1, 2, 3]\n"
    )
    with pytest.raises(ModelCatalogError, match="'ratings'"):
        load_model_catalog(path)


def test_task_profile_non_numeric_weight_raises(tmp_path):
    path = _write(
        tmp_path,
        "models:\n  - name: foo\n    model: openai/gpt-4o\n    ratings: {cost: 5}\n"
        "task_profiles:\n  bogus:\n    weights: {cost: lots}\n",
    )
    with pytest.raises(ModelCatalogError, match="weight"):
        load_model_catalog(path)


# --- classify_task ---------------------------------------------------------


def _catalog_with_profiles(*profiles: TaskProfile) -> ModelCatalog:
    return ModelCatalog(
        models=[ModelCatalogEntry(name="x", model="openai/gpt-4o")],
        task_profiles=list(profiles),
    )


def test_classify_task_matches_a_trigger_case_insensitively():
    debugging = TaskProfile(name="debugging", triggers=("fix", "bug"), weights={"reasoning": 3})
    default = TaskProfile(name="default", weights={"reasoning": 1})
    catalog = _catalog_with_profiles(debugging, default)

    profile = classify_task("Please FIX the crash in the parser", catalog)

    assert profile.name == "debugging"


def test_classify_task_first_declared_match_wins():
    first = TaskProfile(name="first", triggers=("fix",), weights={"a": 1})
    second = TaskProfile(name="second", triggers=("fix",), weights={"a": 2})
    catalog = _catalog_with_profiles(first, second)

    profile = classify_task("please fix this", catalog)

    assert profile.name == "first"


def test_classify_task_falls_back_to_default_profile():
    debugging = TaskProfile(name="debugging", triggers=("fix",), weights={"reasoning": 3})
    default = TaskProfile(name="default", weights={"reasoning": 1})
    catalog = _catalog_with_profiles(debugging, default)

    profile = classify_task("Write a haiku about clouds", catalog)

    assert profile.name == "default"


def test_classify_task_with_no_default_profile_synthesizes_empty_weights():
    debugging = TaskProfile(name="debugging", triggers=("fix",), weights={"reasoning": 3})
    catalog = _catalog_with_profiles(debugging)

    profile = classify_task("Write a haiku about clouds", catalog)

    assert profile.weights == {}


# --- score_entry / rank_candidates -----------------------------------------


def test_score_entry_is_a_weighted_sum():
    entry = ModelCatalogEntry(name="x", model="openai/gpt-4o", ratings={"reasoning": 4, "cost": 2})

    score = score_entry(entry, {"reasoning": 3, "cost": 1})

    assert score == 4 * 3 + 2 * 1


def test_score_entry_treats_a_missing_axis_as_zero():
    entry = ModelCatalogEntry(name="x", model="openai/gpt-4o", ratings={"reasoning": 4})

    score = score_entry(entry, {"reasoning": 1, "cost": 100})

    assert score == 4


def test_rank_candidates_sorts_descending_by_score():
    cheap = ModelCatalogEntry(name="cheap", model="a", ratings={"reasoning": 1, "cost": 5})
    smart = ModelCatalogEntry(name="smart", model="b", ratings={"reasoning": 5, "cost": 1})
    catalog = ModelCatalog(models=[cheap, smart], task_profiles=[])

    ranked = rank_candidates(catalog, {"reasoning": 1, "cost": 0})

    assert [e.name for e in ranked] == ["smart", "cheap"]


def test_rank_candidates_breaks_ties_by_declared_order():
    a = ModelCatalogEntry(name="a", model="x", ratings={"reasoning": 3})
    b = ModelCatalogEntry(name="b", model="y", ratings={"reasoning": 3})
    catalog = ModelCatalog(models=[a, b], task_profiles=[])

    ranked = rank_candidates(catalog, {"reasoning": 1})

    assert [e.name for e in ranked] == ["a", "b"]


def test_rank_candidates_works_with_a_single_model_catalog():
    only = ModelCatalogEntry(name="only", model="x", ratings={"reasoning": 3})
    catalog = ModelCatalog(models=[only], task_profiles=[])

    ranked = rank_candidates(catalog, {"reasoning": 1})

    assert ranked == [only]


def test_rank_candidates_excludes_deactivated_models():
    active = ModelCatalogEntry(name="active", model="x", ratings={"reasoning": 1})
    inactive = ModelCatalogEntry(
        name="inactive", model="y", ratings={"reasoning": 5}, activated=False
    )
    catalog = ModelCatalog(models=[active, inactive], task_profiles=[])

    ranked = rank_candidates(catalog, {"reasoning": 1})

    # inactive scores higher (5 vs 1) but must still be excluded entirely.
    assert ranked == [active]


def test_rank_candidates_raises_when_every_model_is_deactivated():
    only = ModelCatalogEntry(name="only", model="x", ratings={"reasoning": 3}, activated=False)
    catalog = ModelCatalog(models=[only], task_profiles=[])

    with pytest.raises(ModelCatalogError, match="activated"):
        rank_candidates(catalog, {"reasoning": 1})
