"""Builds the SDK's LLM object from our Config.

The model-agnostic invariant lives in config.py: provider/model selection is
entirely a function of `cfg.model`'s LiteLLM-style prefix (e.g. "anthropic/...",
"openai/...", "ollama/..."). Nothing here branches on provider.
"""

from __future__ import annotations

from pydantic import SecretStr

from openhands.sdk import LLM

from .config import Config


def build_llm(cfg: Config) -> LLM:
    return LLM(
        usage_id="harness",
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=SecretStr(cfg.api_key) if cfg.api_key else None,
    )
