"""Tests for model_selection.py: ModelChain state machine, llm_for_entry's
reuse of override_llm/build_llm, and write_model_decisions' output — no
network, no real Conversation.
"""

from __future__ import annotations

from harness.config import Config
from harness.model_catalog import ModelCatalogEntry
from harness.model_selection import ModelChain, llm_for_entry, write_model_decisions


def _cfg(**overrides) -> Config:
    base = dict(
        model="anthropic/claude-sonnet-4-5-20250929",
        api_key="sk-anthropic",
        base_url=None,
        workspace=".",
        max_iterations=10,
        confirm_mode="never",
        execution="local",
    )
    base.update(overrides)
    return Config(**base)


def _entry(name: str, **overrides) -> ModelCatalogEntry:
    base = dict(name=name, model="openai/gpt-4o", ratings={"reasoning": 3})
    base.update(overrides)
    return ModelCatalogEntry(**base)


# --- ModelChain --------------------------------------------------------


def test_chain_current_starts_at_index_zero_by_default():
    chain = ModelChain([_entry("a"), _entry("b")], task_profile="default", weights={})

    assert chain.current.name == "a"
    assert chain.decisions == []


def test_chain_can_start_at_a_different_index():
    chain = ModelChain(
        [_entry("a"), _entry("b")], task_profile="default", weights={}, start_index=1
    )

    assert chain.current.name == "b"


def test_chain_requires_at_least_one_candidate():
    import pytest

    with pytest.raises(ValueError, match="at least one candidate"):
        ModelChain([], task_profile="default", weights={})


def test_record_initial_logs_a_decision_without_moving_the_index():
    chain = ModelChain([_entry("a"), _entry("b")], task_profile="debugging", weights={"x": 1})

    chain.record_initial("Best fit for this task")

    assert chain.current.name == "a"
    assert len(chain.decisions) == 1
    record = chain.decisions[0]
    assert record.kind == "initial"
    assert record.chosen == "a"
    assert record.task_profile == "debugging"
    assert record.reason == "Best fit for this task"


def test_advance_moves_to_the_next_candidate_and_logs_it():
    chain = ModelChain([_entry("a"), _entry("b"), _entry("c")], task_profile="default", weights={})

    next_entry = chain.advance(kind="escalation_api_failure", reason="auth error")

    assert next_entry.name == "b"
    assert chain.current.name == "b"
    assert len(chain.decisions) == 1
    assert chain.decisions[0].kind == "escalation_api_failure"
    assert chain.decisions[0].chosen == "b"


def test_advance_returns_none_once_exhausted_and_current_stays_put():
    chain = ModelChain([_entry("a"), _entry("b")], task_profile="default", weights={})
    chain.advance(kind="escalation_api_failure", reason="first failure")

    result = chain.advance(kind="escalation_api_failure", reason="second failure")

    assert result is None
    assert chain.current.name == "b"
    assert len(chain.decisions) == 1  # the failed advance did not log anything


def test_exhausted_property_reflects_position():
    chain = ModelChain([_entry("a"), _entry("b")], task_profile="default", weights={})

    assert chain.exhausted is False
    chain.advance(kind="escalation_api_failure", reason="x")
    assert chain.exhausted is True


def test_single_candidate_chain_is_immediately_exhausted():
    chain = ModelChain([_entry("only")], task_profile="default", weights={})

    assert chain.exhausted is True
    assert chain.advance(kind="escalation_api_failure", reason="x") is None


# --- llm_for_entry -------------------------------------------------------


def test_llm_for_entry_uses_the_entrys_own_model_and_key():
    cfg = _cfg()
    entry = _entry(
        "deep-reasoner",
        model="anthropic/claude-opus-4",
        api_key="sk-opus",
        reasoning_effort="high",
    )

    llm = llm_for_entry(cfg, entry, usage_id="harness:deep-reasoner")

    assert llm.model == "anthropic/claude-opus-4"
    assert llm.usage_id == "harness:deep-reasoner"
    assert llm.reasoning_effort == "high"


def test_llm_for_entry_clears_api_key_for_a_keyless_entry():
    # cfg's own api_key is for a different provider (Anthropic) — an entry
    # with no api_key of its own (e.g. a local Ollama model) must not
    # silently inherit it. Uses a made-up model name (not "ollama/llama3")
    # so the SDK's own context-window lookup for a *real* small-context
    # local model doesn't fail construction — irrelevant to what's tested
    # here (the api_key-clearing behavior).
    cfg = _cfg(api_key="sk-anthropic-should-not-leak")
    entry = _entry(
        "ollama-local", model="ollama/harness-test-model", base_url="http://localhost:11434"
    )

    llm = llm_for_entry(cfg, entry, usage_id="harness:ollama-local")

    assert llm.api_key is None
    assert llm.base_url == "http://localhost:11434"


def test_llm_for_entry_clears_base_url_when_entry_does_not_set_one():
    cfg = _cfg(base_url="http://leftover-from-a-previous-entry:11434")
    entry = _entry("hosted-model", model="openai/gpt-4o", api_key="sk-openai")

    llm = llm_for_entry(cfg, entry, usage_id="harness:hosted-model")

    assert llm.base_url is None


# --- write_model_decisions -------------------------------------------------


def test_write_model_decisions_is_a_noop_with_no_decisions(tmp_path):
    write_model_decisions(str(tmp_path), [])

    assert not (tmp_path / "MODEL_DECISIONS.md").exists()


def test_write_model_decisions_writes_readable_markdown(tmp_path):
    chain = ModelChain(
        [_entry("balanced", ratings={"reasoning": 4}), _entry("cheap", ratings={"reasoning": 2})],
        task_profile="debugging",
        weights={"reasoning": 1},
    )
    chain.record_initial("Best fit for this task")
    chain.advance(kind="escalation_quality_failure", reason="verification failed twice")

    write_model_decisions(str(tmp_path), chain.decisions)

    content = (tmp_path / "MODEL_DECISIONS.md").read_text()
    assert "# Model Decisions" in content
    assert "initial" in content
    assert "escalation_quality_failure" in content
    assert "balanced" in content
    assert "cheap" in content
    assert "verification failed twice" in content


def test_write_model_decisions_never_includes_secrets(tmp_path):
    entry = _entry("has-a-key", api_key="sk-super-secret", base_url="https://internal.example")
    chain = ModelChain([entry], task_profile="default", weights={})
    chain.record_initial("only candidate")

    write_model_decisions(str(tmp_path), chain.decisions)

    content = (tmp_path / "MODEL_DECISIONS.md").read_text()
    assert "sk-super-secret" not in content
    assert "internal.example" not in content


def test_write_model_decisions_overwrites_rather_than_duplicating_the_header(tmp_path):
    chain = ModelChain([_entry("a"), _entry("b")], task_profile="default", weights={})
    chain.record_initial("first")
    write_model_decisions(str(tmp_path), chain.decisions)
    chain.advance(kind="escalation_api_failure", reason="second")

    write_model_decisions(str(tmp_path), chain.decisions)

    content = (tmp_path / "MODEL_DECISIONS.md").read_text()
    assert content.count("# Model Decisions") == 1
    assert "initial" in content
    assert "escalation_api_failure" in content
