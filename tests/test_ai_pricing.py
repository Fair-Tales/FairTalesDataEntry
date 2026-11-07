"""Offline provider usage and pricing checks for the shared accounting helper."""

from types import SimpleNamespace

import pytest

from ai_pricing import (
    PRICING_PER_MTOK, UsageAccumulator, format_cost_report, normalize_usage,
    usage_cost, usage_metrics,
)


def sdk_object(value):
    """Represent nested fixture dictionaries as SDK-style attribute objects."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: sdk_object(item) for key, item in value.items()})
    return value


@pytest.mark.parametrize("model, expected", [
    ("claude-sonnet-5", 0.00435),
    ("claude-sonnet-4-6", 0.006525),
    ("claude-opus-4-8", 0.010875),
    ("claude-haiku-4-5", 0.002175),
])
def test_existing_anthropic_costs_are_unchanged(model, expected):
    assert usage_cost(model, 1000, 200, 500, 100) == pytest.approx(expected)


@pytest.mark.parametrize("model, input_cost, cached_cost, output_cost", [
    ("gpt-6-astra", .01, .001, .05),
    ("gpt-6.1-sol", .002, .0001, .01),
    ("gpt-6.1-terra", .001, .00005, .005),
    ("gpt-6-luna", .0001, .00001, .0005),
])
def test_frontier_standard_rates(model, input_cost, cached_cost, output_cost):
    """Each 1,000-token bucket uses its own published rate, checked 2026-11-07."""
    assert usage_cost(model, input_tokens=1000) == pytest.approx(input_cost)
    assert usage_cost(model, cache_read=1000) == pytest.approx(cached_cost)
    assert usage_cost(model, output_tokens=1000) == pytest.approx(output_cost)
    # Write retention is not available to distinguish the published tariffs.
    assert usage_cost(model, cache_write=1) is None


@pytest.mark.parametrize("model, short_context_cost", [
    ("gpt-6-astra", .925),
    ("gpt-6.1-sol", .165),
    ("gpt-6.1-terra", .0825),
    ("gpt-6-luna", .00925),
])
@pytest.mark.parametrize("total_input", [272_000, 272_001])
def test_frontier_long_context_is_counted_but_unpriced(model, short_context_cost, total_input):
    # The threshold includes cached input, even when ordinary input is small.
    _, metrics = usage_metrics({
        "input_tokens": total_input, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 200_000},
    }, model)
    assert metrics["input_tokens"] == total_input - 200_000
    assert metrics["cache_read_tokens"] == 200_000
    assert metrics["output_tokens"] == 100
    assert "unknown_usage_calls" not in metrics
    if total_input > 272_000:
        assert metrics["unpriced_calls"] == 1
        assert "cost_usd" not in metrics
    else:
        assert metrics["cost_usd"] == pytest.approx(short_context_cost)
        assert "unpriced_calls" not in metrics


@pytest.mark.parametrize("cached_tokens", [0, 200])
def test_missing_cache_rate_is_never_inferred(monkeypatch, cached_tokens):
    model = "gpt-6.1-terra"
    monkeypatch.setitem(PRICING_PER_MTOK, model,
                        {**PRICING_PER_MTOK[model], "cache_read": None})
    provider, metrics = usage_metrics({
        "input_tokens": 1000, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": cached_tokens},
    }, model)
    assert provider == "openai"
    assert metrics["input_tokens"] == 1000 - cached_tokens
    assert metrics["cache_read_tokens"] == cached_tokens
    assert "unknown_usage_calls" not in metrics
    if cached_tokens:
        assert metrics["unpriced_calls"] == 1
        assert "cost_usd" not in metrics
    else:
        assert metrics["cost_usd"] == pytest.approx(.0015)
        assert "unpriced_calls" not in metrics


@pytest.mark.parametrize("wrap", [dict, sdk_object])
def test_anthropic_usage_keeps_cache_tokens_separate(wrap):
    usage = wrap({
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 100,
    })
    assert normalize_usage(usage, "anthropic-messages") == {
        "input": 1000, "output": 200, "cache_read": 500, "cache_write": 100,
    }


@pytest.mark.parametrize("wrap", [dict, sdk_object])
@pytest.mark.parametrize("fields", [
    {"input_tokens": 1000, "output_tokens": 100,
     "input_tokens_details": {"cached_tokens": 200}},
    {"prompt_tokens": 1000, "completion_tokens": 100,
     "prompt_tokens_details": {"cached_tokens": 200}},
])
def test_openai_usage_subtracts_cached_input_once(wrap, fields):
    counts = normalize_usage(wrap(fields), "openai")
    assert counts == {
        "input": 800, "output": 100, "cache_read": 200, "cache_write": 0,
    }
    tokens = (counts["input"], counts["output"], counts["cache_read"], counts["cache_write"])
    assert usage_cost("gpt-4.1", *tokens) == pytest.approx(0.0025)
    assert usage_cost("gpt-4.1-mini", *tokens) == pytest.approx(0.0005)


def test_anthropic_optional_cache_fields_still_default_to_zero():
    counts = normalize_usage({"input_tokens": 20, "output_tokens": 10})
    assert counts == {"input": 20, "output": 10, "cache_read": 0, "cache_write": 0}


@pytest.mark.parametrize("details", [{}, {"input_tokens_details": None},
                                     {"input_tokens_details": {}}])
def test_missing_openai_cache_usage_is_unknown(details):
    counts = normalize_usage({"input_tokens": 1000, "output_tokens": 100,
                              **details}, "openai")
    assert counts["cache_read"] is None
    assert counts["output"] == 100
    assert usage_cost("gpt-4.1", counts["input"], counts["output"],
                      counts["cache_read"], counts["cache_write"]) is None


def test_missing_usage_and_missing_required_counts_are_unknown():
    assert normalize_usage(None) == {
        "input": None, "output": None, "cache_read": None, "cache_write": None,
    }
    assert normalize_usage({"input_tokens": 0})["output"] is None
    assert usage_cost("claude-sonnet-4-6", 0, None) is None
    assert usage_cost("claude-sonnet-4-6", 0, 0) == 0


@pytest.mark.parametrize("bad_count", [-1, True, 1.5, "100"])
def test_invalid_token_counts_are_rejected(bad_count):
    with pytest.raises(ValueError):
        normalize_usage({"input_tokens": bad_count, "output_tokens": 1})


@pytest.mark.parametrize("usage", ["not usage", [], 1])
def test_nonobject_usage_is_rejected(usage):
    with pytest.raises(ValueError):
        normalize_usage(usage)


def test_cached_input_cannot_exceed_total_openai_input():
    with pytest.raises(ValueError):
        normalize_usage({"input_tokens": 10, "output_tokens": 1,
                         "input_tokens_details": {"cached_tokens": 11}}, "openai")


@pytest.mark.parametrize("model", [
    "future-model", "gpt-6-astra-unverified", "GPT-6 Astra",
])
def test_unknown_model_is_unpriced(caplog, model):
    assert usage_cost(model, 100, 10) is None
    assert model in caplog.text


def test_accumulator_reports_incomplete_cost_but_keeps_known_tokens():
    accumulator = UsageAccumulator()
    accumulator.record("ocr", "claude-sonnet-4-6", {
        "input_tokens": 1000, "output_tokens": 200,
        "cache_read_input_tokens": 500, "cache_creation_input_tokens": 100,
    })
    accumulator.record("ocr", "future-model", {
        "input_tokens": 20, "output_tokens": 10,
    })
    accumulator.record("ocr", "gpt-4.1", None, provider="openai")

    assert accumulator.total_calls == 3
    assert accumulator.total_cost == pytest.approx(0.006525)
    assert accumulator.total_unpriced_calls == 2
    assert accumulator.total_unknown_usage_calls == 1
    assert accumulator.by_operation()["ocr"]["input"] == 1020
    assert accumulator.by_operation()["ocr"]["output"] == 210
    assert "incomplete" in format_cost_report(accumulator, books=1).lower()


def test_accumulator_records_openai_and_logs_malformed_usage(caplog):
    accumulator = UsageAccumulator()
    accumulator.record("relations", "gpt-4.1", {
        "input_tokens": 1000, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 200},
    }, provider="openai")
    accumulator.record("relations", "gpt-4.1", "broken", provider="openai")
    assert accumulator.total_calls == 1
    assert accumulator.total_cost == pytest.approx(0.0025)
    assert accumulator.total_unpriced_calls == 0
    assert accumulator.total_unknown_usage_calls == 0
    assert caplog.records


def test_explicit_normalized_counts_do_not_turn_unknown_cache_into_zero():
    accumulator = UsageAccumulator()
    accumulator.add("ocr", "gpt-4.1", 100, 10, cache_read=None, provider="openai")
    assert accumulator.total_calls == 1
    assert accumulator.total_unpriced_calls == 1
    assert accumulator.total_unknown_usage_calls == 1
    assert accumulator.by_operation()["ocr"]["input"] == 100


@pytest.mark.parametrize("extra_usage", [
    {"server_tool_use": {"web_search_requests": 1}},
    {"server_tool_use": SimpleNamespace(web_search_requests=1, web_fetch_requests=0)},
    {"cache_creation": {"ephemeral_1h_input_tokens": 20}},
])
def test_unpriced_billable_features_keep_tokens_and_flag_cost(extra_usage):
    accumulator = UsageAccumulator()
    accumulator.record("lookup", "claude-sonnet-4-6", {
        "input_tokens": 100, "output_tokens": 10, **extra_usage,
    })
    assert accumulator.total_unpriced_calls == 1
    assert accumulator.total_unknown_usage_calls == 0
    assert accumulator.by_operation()["lookup"]["input"] == 100
    assert "incomplete" in format_cost_report(accumulator).lower()


def test_zero_server_tool_counts_do_not_make_token_cost_unknown():
    accumulator = UsageAccumulator()
    accumulator.record("ocr", "claude-sonnet-4-6", {
        "input_tokens": 100, "output_tokens": 10,
        "server_tool_use": SimpleNamespace(web_search_requests=0, web_fetch_requests=0),
    })
    assert accumulator.total_unpriced_calls == 0
    assert accumulator.total_cost == pytest.approx(.00045)
