"""Pure data model, parsing, and scoring for `HARNESS_MODEL_SELECTION=auto`'s
model catalog (`models.yaml`) — no SDK imports, no side effects beyond
reading the one file, fully unit-testable without a `Conversation`.

`models.yaml` is git-ignored (see `.gitignore`) because real API keys live
directly in it — same treatment as `.env`. `models.yaml.example` is the
checked-in template. See MANUAL.md "Automatic model selection" for the
user-facing writeup and ROADMAP.md's decisions log for the design history
(deterministic weighted scoring was chosen over an LLM-based router; one
static ranked list per task, not re-scored mid-task; no hard minimum catalog
size — see `rank_candidates`).

Rating/weight axes are deliberately open-ended, not hardcoded to a fixed set
(`reasoning`/`cost`/`precision`/`code` are just the template's example) —
scoring iterates whatever keys a task profile's `weights` declares, so a
user can add a new axis with zero code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ModelCatalogError(ValueError):
    """Raised when `models.yaml` is missing, malformed, or fails schema
    validation. Message is user-facing."""


@dataclass(frozen=True)
class ModelCatalogEntry:
    """One candidate model. `api_key`/`base_url`/`reasoning_effort` mirror
    the exact same fields `.env`'s LLM_API_KEY/LLM_BASE_URL/
    LLM_REASONING_EFFORT carry for the single-model default — see
    `model_selection.py`'s `llm_for_entry()`, which builds an actual `LLM`
    from one of these via `config.override_llm()`, not a separate builder.
    """

    name: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    description: str = ""
    ratings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskProfile:
    """A named weight vector, selected by keyword match against the task
    text — same `KeywordTrigger`-style deterministic matching this project's
    skills already use, not an LLM classification call."""

    name: str
    triggers: tuple[str, ...] = ()
    weights: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCatalog:
    models: list[ModelCatalogEntry]
    task_profiles: list[TaskProfile]


_DEFAULT_PROFILE = TaskProfile(name="default", triggers=(), weights={})


def _require_str(data: dict, key: str, *, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogError(f"{context}: {key!r} must be a non-empty string.")
    return value


def _optional_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelCatalogError(f"{key!r} must be a string if given.")
    value = value.strip()
    return value or None


def _parse_ratings(data: dict, *, context: str) -> dict[str, float]:
    raw = data.get("ratings", {}) or {}
    if not isinstance(raw, dict):
        raise ModelCatalogError(f"{context}: 'ratings' must be a mapping of axis -> number.")
    ratings: dict[str, float] = {}
    for axis, value in raw.items():
        try:
            ratings[str(axis)] = float(value)
        except (TypeError, ValueError):
            raise ModelCatalogError(
                f"{context}: rating {axis!r} must be a number, got {value!r}."
            ) from None
    return ratings


def _parse_model_entry(data: Any) -> ModelCatalogEntry:
    if not isinstance(data, dict):
        raise ModelCatalogError("Each entry under 'models' must be a mapping.")
    name = _require_str(data, "name", context="A model entry")
    context = f"Model {name!r}"
    model = _require_str(data, "model", context=context)
    return ModelCatalogEntry(
        name=name,
        model=model,
        api_key=_optional_str(data, "api_key"),
        base_url=_optional_str(data, "base_url"),
        reasoning_effort=_optional_str(data, "reasoning_effort"),
        description=(_optional_str(data, "description") or ""),
        ratings=_parse_ratings(data, context=context),
    )


def _parse_weights(data: dict, *, context: str) -> dict[str, float]:
    raw = data.get("weights", {}) or {}
    if not isinstance(raw, dict):
        raise ModelCatalogError(f"{context}: 'weights' must be a mapping of axis -> number.")
    weights: dict[str, float] = {}
    for axis, value in raw.items():
        try:
            weights[str(axis)] = float(value)
        except (TypeError, ValueError):
            raise ModelCatalogError(
                f"{context}: weight {axis!r} must be a number, got {value!r}."
            ) from None
    return weights


def _parse_task_profile(name: str, data: Any) -> TaskProfile:
    if not isinstance(data, dict):
        raise ModelCatalogError(f"Task profile {name!r} must be a mapping.")
    triggers_raw = data.get("triggers", []) or []
    if not isinstance(triggers_raw, list) or not all(isinstance(t, str) for t in triggers_raw):
        raise ModelCatalogError(f"Task profile {name!r}: 'triggers' must be a list of strings.")
    return TaskProfile(
        name=name,
        triggers=tuple(triggers_raw),
        weights=_parse_weights(data, context=f"Task profile {name!r}"),
    )


def load_model_catalog(path: str) -> ModelCatalog:
    """Parse `models.yaml`. Raises `ModelCatalogError` for a missing file, a
    file that isn't valid YAML, or one that fails schema validation (a
    non-empty 'models' list, each with 'name'/'model'; profiles are
    optional). No minimum catalog size is enforced — see
    `rank_candidates()`'s docstring for why."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ModelCatalogError(
            f"HARNESS_MODEL_SELECTION=auto requires a model catalog at {path!r}, "
            "but no such file exists. Copy models.yaml.example to models.yaml "
            "and fill in your models."
        )
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ModelCatalogError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ModelCatalogError(f"{path} must contain a mapping at the top level.")

    models_raw = raw.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise ModelCatalogError(f"{path} must define a non-empty 'models' list.")
    models = [_parse_model_entry(entry) for entry in models_raw]

    seen_names = set()
    for entry in models:
        if entry.name in seen_names:
            raise ModelCatalogError(f"{path}: duplicate model name {entry.name!r}.")
        seen_names.add(entry.name)

    profiles_raw = raw.get("task_profiles", {}) or {}
    if not isinstance(profiles_raw, dict):
        raise ModelCatalogError(f"{path}: 'task_profiles' must be a mapping of name -> profile.")
    task_profiles = [_parse_task_profile(name, data) for name, data in profiles_raw.items()]

    return ModelCatalog(models=models, task_profiles=task_profiles)


def classify_task(task_text: str, catalog: ModelCatalog) -> TaskProfile:
    """First declared task profile whose trigger appears (case-insensitive
    substring match) in `task_text` wins — same tie-break convention as
    `_LANGUAGE_MARKERS` in `run_tests_tool.py`. Falls back to a profile named
    `default` if one is declared, else a synthesized empty-weights profile
    (every model scores 0 and the declared order breaks the tie — see
    `rank_candidates`).
    """
    lowered = task_text.lower()
    for profile in catalog.task_profiles:
        if profile.name == "default":
            continue
        if any(trigger.lower() in lowered for trigger in profile.triggers):
            return profile
    for profile in catalog.task_profiles:
        if profile.name == "default":
            return profile
    return _DEFAULT_PROFILE


def score_entry(entry: ModelCatalogEntry, weights: dict[str, float]) -> float:
    """Weighted sum over whatever axes `weights` declares — an axis a model
    doesn't rate itself on contributes 0, not an error."""
    return sum(entry.ratings.get(axis, 0.0) * weight for axis, weight in weights.items())


def rank_candidates(catalog: ModelCatalog, weights: dict[str, float]) -> list[ModelCatalogEntry]:
    """Every catalog model, scored against `weights` and sorted descending
    (stable — ties keep the file's declared order, via Python's stable sort
    over the already-declaration-ordered `catalog.models`).

    No minimum length is enforced: a 1- or 2-model catalog just produces a
    correspondingly short fallback chain, never an error — a user is free to
    add more models later without hitting a hidden validation cliff.
    """
    return sorted(catalog.models, key=lambda entry: score_entry(entry, weights), reverse=True)
