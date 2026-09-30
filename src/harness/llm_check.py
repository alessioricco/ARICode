"""Connectivity check for the configured LLM: one tiny, direct completion.

Used by `harness-admin check-llm`. Builds the model exactly as a real run
does (`llm.build_llm`, so `LLM_MODEL`/`LLM_API_KEY`/`LLM_BASE_URL`/
`LLM_REASONING_EFFORT` and any per-call override apply unchanged), but with
retries disabled and a short timeout: a health check should fail fast and
say why, not sit through the SDK's default 5 retries with up to 64s waits.

Passes only when the model returns non-empty text. Never prints the API key:
provider error text is scrubbed of it before being returned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from litellm.exceptions import NotFoundError as LiteLLMNotFoundError
from openhands.sdk import Message, TextContent
from openhands.sdk.llm.exceptions.types import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)

from .config import Config
from .llm import build_llm

CHECK_PROMPT = "This is a connectivity check. Reply with exactly: OK"
DEFAULT_TIMEOUT_SECONDS = 60

# (exception type, short kind, actionable hint) — checked in order.
_ERROR_HINTS: tuple[tuple[type[Exception], str, str], ...] = (
    (
        LLMAuthenticationError,
        "authentication",
        (
            "The provider rejected the credentials — check LLM_API_KEY matches "
            "the provider in LLM_MODEL's prefix."
        ),
    ),
    (
        LLMRateLimitError,
        "rate_limit",
        "The provider is rate-limiting or the account is out of quota/credit.",
    ),
    (
        LLMTimeoutError,
        "timeout",
        (
            "No answer within the timeout — check LLM_BASE_URL / network, or "
            "retry with a larger --timeout."
        ),
    ),
    (
        LLMServiceUnavailableError,
        "unavailable",
        (
            "The provider/endpoint is unreachable or down — check LLM_BASE_URL "
            "and that a local server (e.g. Ollama) is running."
        ),
    ),
    (
        # Not mapped by the SDK: a direct completion() lets LiteLLM's own
        # NotFoundError through (confirmed live with an unknown model id).
        LiteLLMNotFoundError,
        "model_not_found",
        (
            "The provider doesn't know this model id, or the key has no access "
            "to it — check the spelling in LLM_MODEL against the provider's "
            "current model list."
        ),
    ),
    (
        LLMBadRequestError,
        "bad_request",
        (
            "The request was rejected — often an unknown model id or an option "
            "the model doesn't support (e.g. LLM_REASONING_EFFORT)."
        ),
    ),
)


_SETUP_HINT = (
    "The SDK rejected this model configuration before sending any request; "
    "a real run would fail the same way."
)


class _SetupError(Exception):
    """Marks a failure while building the LLM object, not while calling it."""

    def __init__(self, original: Exception) -> None:
        super().__init__(str(original))
        self.original = original


@dataclass(frozen=True)
class LLMCheckResult:
    ok: bool
    model: str
    base_url: str | None
    latency_seconds: float
    reply: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    # a kind from _ERROR_HINTS, else "setup" / "no_reply" / "error"
    error_kind: str | None = None
    error: str | None = None
    hint: str | None = None


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def check_llm(cfg: Config, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> LLMCheckResult:
    """Send `CHECK_PROMPT` once to the configured model and report the result."""
    started = time.monotonic()
    try:
        # Inside the try: the SDK validates some settings when the LLM object
        # is *built* (e.g. it rejects a context window below its 16K minimum,
        # confirmed live with ollama/llama3), before any request is sent —
        # the same error would stop a real run, so it's a check failure too.
        try:
            llm = build_llm(cfg, usage_id="check-llm").model_copy(
                update={"num_retries": 0, "timeout": timeout}
            )
        except Exception as exc:
            raise _SetupError(exc) from exc
        response = llm.completion(
            messages=[Message(role="user", content=[TextContent(text=CHECK_PROMPT)])]
        )
    except Exception as exc:  # noqa: BLE001 - every failure becomes a reported result
        kind, hint = "error", None
        if isinstance(exc, _SetupError):
            exc = exc.original
            kind, hint = "setup", _SETUP_HINT
        for exc_type, exc_kind, exc_hint in _ERROR_HINTS:
            if isinstance(exc, exc_type):
                kind, hint = exc_kind, exc_hint
                break
        return LLMCheckResult(
            ok=False,
            model=cfg.model,
            base_url=cfg.base_url,
            latency_seconds=time.monotonic() - started,
            error_kind=kind,
            error=_scrub(f"{type(exc).__name__}: {exc}", cfg.api_key),
            hint=hint,
        )
    latency = time.monotonic() - started

    reply = " ".join(
        text.strip()
        for content in response.message.content
        if (text := getattr(content, "text", None)) and text.strip()
    )
    usage = response.metrics.accumulated_token_usage
    return LLMCheckResult(
        ok=bool(reply),
        model=cfg.model,
        base_url=cfg.base_url,
        latency_seconds=latency,
        reply=reply,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        cost=response.metrics.accumulated_cost,
        error_kind=None if reply else "no_reply",
        error=None if reply else "The model answered, but with no text content.",
        hint=None,
    )
