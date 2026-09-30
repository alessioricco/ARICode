"""End-of-run summary: wall-clock time, models used, tokens, and cost.

Pure data shaping, no SDK import: `runner.py` extracts the SDK's
`conversation.conversation_stats.usage_to_metrics` into plain dicts
(`Metrics.get()`, keyed by LLM `usage_id`) and passes them here, the same
split `artifacts.py` uses. The result is attached to `TaskOutcome`, printed
by `cli.py`, and written into the run artifacts.

Per-model, not the SDK's combined metrics: the combined entry reports its
model as `"default"`, while each per-`usage_id` entry carries the real
model name (auto model selection gives every candidate its own usage_id).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelUsage:
    model: str
    prompt_tokens: int = 0  # includes cache_read_tokens
    completion_tokens: int = 0  # includes reasoning_tokens
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_known(self) -> bool:
        # LiteLLM reports 0.0 when it has no pricing for a model (e.g. a
        # local endpoint) — that's "unknown", not "free".
        return self.cost > 0 or self.total_tokens == 0


@dataclass(frozen=True)
class RunSummary:
    duration_seconds: float
    models: tuple[ModelUsage, ...] = ()

    @property
    def total_tokens(self) -> int:
        return sum(m.total_tokens for m in self.models)

    @property
    def prompt_tokens(self) -> int:
        return sum(m.prompt_tokens for m in self.models)

    @property
    def completion_tokens(self) -> int:
        return sum(m.completion_tokens for m in self.models)

    @property
    def cache_read_tokens(self) -> int:
        return sum(m.cache_read_tokens for m in self.models)

    @property
    def total_cost(self) -> float:
        return sum(m.cost for m in self.models)

    @property
    def cost_complete(self) -> bool:
        """False when any model used has no pricing data (total is a lower bound)."""
        return all(m.cost_known for m in self.models)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_seconds": round(self.duration_seconds, 3),
            "models": [
                {
                    "model": m.model,
                    "prompt_tokens": m.prompt_tokens,
                    "completion_tokens": m.completion_tokens,
                    "cache_read_tokens": m.cache_read_tokens,
                    "reasoning_tokens": m.reasoning_tokens,
                    "total_tokens": m.total_tokens,
                    "cost": m.cost,
                    "cost_known": m.cost_known,
                }
                for m in self.models
            ],
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_cost": self.total_cost,
            "cost_complete": self.cost_complete,
        }

    def format_lines(self) -> list[str]:
        lines = [f"Total running time: {format_duration(self.duration_seconds)}"]
        if not self.models:
            lines.append("Models: none (no LLM calls were recorded)")
        else:
            lines.append("Models: " + ", ".join(m.model for m in self.models))
        lines.append(
            f"Total tokens: {self.total_tokens:,} "
            f"(input {self.prompt_tokens:,}, of which cached {self.cache_read_tokens:,}; "
            f"output {self.completion_tokens:,})"
        )
        if self.cost_complete:
            lines.append(f"Total cost: ${self.total_cost:.4f}")
        else:
            unpriced = ", ".join(m.model for m in self.models if not m.cost_known)
            lines.append(
                f"Total cost: ${self.total_cost:.4f} + unknown (no pricing data for {unpriced})"
            )
        if len(self.models) > 1:
            for m in self.models:
                cost = f"${m.cost:.4f}" if m.cost_known else "cost unknown"
                lines.append(f"  - {m.model}: {m.total_tokens:,} tokens, {cost}")
        return lines


def format_duration(seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{seconds:.1f}s"


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0


def summarize_run(
    duration_seconds: float, per_model_metrics: Mapping[str, Mapping[str, Any]]
) -> RunSummary:
    """Build a `RunSummary` from `{usage_id: Metrics.get()}` dicts.

    Entries with no tokens and no cost (e.g. auto-selection candidates that
    were never actually called) are dropped; entries naming the same model
    are merged. Missing/partial fields count as zero rather than raising —
    this runs in `stream_task`'s `finally` and must never mask the run's own
    outcome or exception.
    """
    merged: dict[str, dict[str, float]] = {}
    for usage_id, metrics in per_model_metrics.items():
        usage = metrics.get("accumulated_token_usage")
        if not isinstance(usage, Mapping):
            usage = {}
        model = usage.get("model")
        if not model or model == "default":
            costs = metrics.get("costs")
            first = costs[0] if isinstance(costs, list) and costs else None
            model = (first.get("model") if isinstance(first, Mapping) else None) or usage_id
        totals = merged.setdefault(
            model,
            {"prompt": 0, "completion": 0, "cache_read": 0, "reasoning": 0, "cost": 0.0},
        )
        totals["prompt"] += _int(usage.get("prompt_tokens"))
        totals["completion"] += _int(usage.get("completion_tokens"))
        totals["cache_read"] += _int(usage.get("cache_read_tokens"))
        totals["reasoning"] += _int(usage.get("reasoning_tokens"))
        cost = metrics.get("accumulated_cost")
        totals["cost"] += float(cost) if isinstance(cost, int | float) else 0.0

    models = tuple(
        ModelUsage(
            model=model,
            prompt_tokens=int(t["prompt"]),
            completion_tokens=int(t["completion"]),
            cache_read_tokens=int(t["cache_read"]),
            reasoning_tokens=int(t["reasoning"]),
            cost=t["cost"],
        )
        for model, t in merged.items()
        if t["prompt"] or t["completion"] or t["cost"]
    )
    return RunSummary(duration_seconds=duration_seconds, models=models)
