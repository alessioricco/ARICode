"""Tests for harness.llm.build_llm — no network, no real API key needed.

Focus: cfg.reasoning_effort's conditional pass-through. The SDK's own
`LLM.reasoning_effort` field defaults to "high"; build_llm must only pass the
kwarg when cfg.reasoning_effort is set, so that default still applies when
it's not (see build_llm's docstring comment for why `reasoning_effort=None`
explicitly would be different from omitting it).
"""

from harness.config import load_config
from harness.llm import build_llm


def _base_env(**overrides: str) -> dict[str, str]:
    env = {"LLM_MODEL": "anthropic/claude-sonnet-4-5-20250929", "LLM_API_KEY": "sk-test"}
    env.update(overrides)
    return env


def test_build_llm_without_reasoning_effort_uses_sdk_default():
    cfg = load_config(_base_env())
    assert cfg.reasoning_effort is None

    llm = build_llm(cfg)

    assert llm.reasoning_effort == "high"


def test_build_llm_with_reasoning_effort_overrides_sdk_default():
    cfg = load_config(_base_env(LLM_REASONING_EFFORT="low"))

    llm = build_llm(cfg)

    assert llm.reasoning_effort == "low"


def test_build_llm_passes_through_model_and_base_url():
    cfg = load_config(_base_env(LLM_BASE_URL="http://localhost:11434"))

    llm = build_llm(cfg)

    assert llm.model == "anthropic/claude-sonnet-4-5-20250929"
    assert llm.base_url == "http://localhost:11434"


def test_build_llm_usage_id_defaults_to_harness():
    cfg = load_config(_base_env())

    llm = build_llm(cfg)

    assert llm.usage_id == "harness"


def test_build_llm_usage_id_is_overridable():
    # Auto model selection (model_selection.py) gives each catalog candidate
    # its own usage_id so the SDK's LLM registry never confuses one
    # candidate's config for another's on a mid-task switch_llm() call.
    cfg = load_config(_base_env())

    llm = build_llm(cfg, usage_id="harness:deep-reasoner")

    assert llm.usage_id == "harness:deep-reasoner"
