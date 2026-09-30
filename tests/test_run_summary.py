"""run_summary.py tests — pure dict shaping, no SDK/LLM. The metrics dicts
mirror the SDK's real `Metrics.get()` shape (as written to metrics.json by a
live run).
"""

from __future__ import annotations

from harness.run_summary import RunSummary, format_duration, summarize_run


def _metrics(model, prompt, completion, cost, cache_read=0, reasoning=0):
    return {
        "accumulated_cost": cost,
        "accumulated_token_usage": {
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cache_read_tokens": cache_read,
            "reasoning_tokens": reasoning,
        },
        "costs": [{"model": model, "cost": cost}],
    }


def test_single_model_totals_match_the_sdk_metrics():
    summary = summarize_run(
        242.6,
        {"harness": _metrics("openai/gpt-5.6-luna", 774063, 27128, 0.0593, 725104, 7828)},
    )

    assert [m.model for m in summary.models] == ["openai/gpt-5.6-luna"]
    assert summary.total_tokens == 774063 + 27128
    assert summary.cache_read_tokens == 725104
    assert summary.total_cost == 0.0593
    assert summary.cost_complete


def test_multiple_models_are_listed_merged_and_unused_candidates_dropped():
    summary = summarize_run(
        10,
        {
            "harness:cheap": _metrics("openai/small", 100, 10, 0.01),
            "harness:balanced": _metrics("anthropic/mid", 200, 20, 0.02),
            "harness:again": _metrics("openai/small", 50, 5, 0.005),
            "harness:never-called": {"accumulated_cost": 0.0, "accumulated_token_usage": None},
        },
    )

    usage = {m.model: m for m in summary.models}
    assert set(usage) == {"openai/small", "anthropic/mid"}
    assert usage["openai/small"].total_tokens == 165
    assert round(summary.total_cost, 6) == 0.035
    assert summary.total_tokens == 385


def test_model_name_falls_back_to_cost_entries_then_usage_id():
    from_costs = _metrics("default", 10, 1, 0.1)
    from_costs["costs"] = [{"model": "gemini/pro", "cost": 0.1}]
    summary = summarize_run(
        1, {"a": from_costs, "b": {"accumulated_cost": 0.2, "accumulated_token_usage": None}}
    )

    assert [m.model for m in summary.models] == ["gemini/pro", "b"]


def test_zero_cost_with_tokens_is_reported_as_unknown_not_free():
    summary = summarize_run(5, {"harness": _metrics("ollama/llama3", 1000, 100, 0.0)})

    assert not summary.cost_complete
    cost_line = next(line for line in summary.format_lines() if line.startswith("Total cost"))
    assert "unknown" in cost_line and "ollama/llama3" in cost_line


def test_format_lines_cover_the_four_requested_measures():
    lines = summarize_run(
        125, {"harness": _metrics("openai/gpt-4o", 1500, 500, 0.0123, cache_read=1000)}
    ).format_lines()

    text = "\n".join(lines)
    assert "Total running time: 2m 05s" in text
    assert "Models: openai/gpt-4o" in text
    assert "Total tokens: 2,000 (input 1,500, of which cached 1,000; output 500)" in text
    assert "Total cost: $0.0123" in text


def test_no_recorded_calls_and_malformed_metrics_do_not_raise():
    summary = summarize_run(3, {"x": {"accumulated_token_usage": "garbage", "costs": "garbage"}})
    assert isinstance(summary, RunSummary)

    empty = summarize_run(3, {})
    assert empty.models == () and empty.total_cost == 0
    assert "none" in empty.format_lines()[1]


def test_to_dict_is_json_ready():
    data = summarize_run(1.23456, {"h": _metrics("m", 1, 2, 0.5)}).to_dict()

    assert data["duration_seconds"] == 1.235
    assert data["total_tokens"] == 3
    assert data["models"][0]["model"] == "m"
    assert data["total_cost"] == 0.5


def test_format_duration():
    assert format_duration(4.26) == "4.3s"
    assert format_duration(61) == "1m 01s"
    assert format_duration(3725) == "1h 02m 05s"
