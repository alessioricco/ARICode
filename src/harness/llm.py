"""Builds the SDK's LLM object from our Config.

The model-agnostic invariant lives in config.py: provider/model selection is
entirely a function of `cfg.model`'s LiteLLM-style prefix (e.g. "anthropic/...",
"openai/...", "ollama/..."). Nothing here branches on provider.
"""

from __future__ import annotations

from pydantic import SecretStr

from openhands.sdk import LLM

from .config import Config


def build_llm(cfg: Config, *, usage_id: str = "harness") -> LLM:
    # Only included when set: the SDK's own `reasoning_effort` field has a
    # real default ("high") on the pydantic model itself, and passing
    # `reasoning_effort=None` explicitly would override that default with an
    # actual None rather than leaving it alone — the field's type is
    # `Literal[...] | str | None`, so None is a distinct, accepted value, not
    # "omitted". Omitting the kwarg entirely is what lets the SDK's own
    # default apply when cfg.reasoning_effort is unset.
    optional: dict[str, str] = {}
    if cfg.reasoning_effort:
        optional["reasoning_effort"] = cfg.reasoning_effort
    return LLM(
        usage_id=usage_id,
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=SecretStr(cfg.api_key) if cfg.api_key else None,
        **optional,
    )
