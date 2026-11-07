"""Provider-native settlement uses shared normalization, with no provider calls."""
from decimal import Decimal

import pytest

from scripts.extraction_eval.gateway import Gateway, usage_charge
from scripts.extraction_eval.store import OutcomeUnknown


def test_anthropic_settlement_preserves_pinned_rates():
    rate = {"input": 2, "output": 8, "cache_read": .2, "cache_write": 2.5}
    usage = {"input_tokens": 100, "output_tokens": 10,
             "cache_read_input_tokens": 20, "cache_creation_input_tokens": 40}
    assert usage_charge({"usage": usage}, rate) == Decimal(".000384")


def test_anthropic_settlement_preserves_uncached_usage():
    rate = {"input": 1, "output": 2, "cache_read": .5, "cache_write": 1.25}
    assert usage_charge({"usage": {"input_tokens": 10, "output_tokens": 5}}, rate) == Decimal(".000020")


@pytest.mark.parametrize(("provider", "usage"), [
    ("openai-responses", {"input_tokens": 100, "output_tokens": 10,
                          "input_tokens_details": {"cached_tokens": 20},
                          "output_tokens_details": {"reasoning_tokens": 4}}),
    ("openai-chat-completions", {"prompt_tokens": 100, "completion_tokens": 10,
                                "prompt_tokens_details": {"cached_tokens": 20},
                                "completion_tokens_details": {"reasoning_tokens": 4}}),
])
def test_openai_settlement_subtracts_cached_input_once(provider, usage):
    rate = {"input": 2, "output": 8, "cache_read": .5, "cache_write": None}
    assert usage_charge({"usage": usage}, rate, provider) == Decimal(".000250")


@pytest.mark.parametrize("usage", [
    None,
    {},
    {"input_tokens": 10},
    {"input_tokens": -1, "output_tokens": 5},
    {"input_tokens": True, "output_tokens": 5},
    {"input_tokens": "10", "output_tokens": 5},
])
def test_unknown_or_malformed_usage_retains_reservation(usage):
    rate = {"input": 1, "output": 2, "cache_read": .5, "cache_write": 1.25}
    with pytest.raises(OutcomeUnknown, match="retaining"):
        usage_charge({"usage": usage}, rate)


def test_missing_rate_for_used_tokens_retains_reservation():
    with pytest.raises(OutcomeUnknown, match="Missing output price"):
        usage_charge({"usage": {"input_tokens": 10, "output_tokens": 5}}, {"input": 1})


@pytest.mark.parametrize("details", [None, {"cached_tokens": 101}])
def test_unknown_or_impossible_openai_cache_usage_retains_reservation(details):
    usage = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": details}
    rate = {"input": 2, "output": 8, "cache_read": .5, "cache_write": None}
    with pytest.raises(OutcomeUnknown, match="retaining"):
        usage_charge({"usage": usage}, rate, "openai-responses")


def test_unexpected_billable_tools_remain_unsettled():
    usage = {"input_tokens": 10, "output_tokens": 5,
             "server_tool_use": {"web_search_requests": 1}}
    with pytest.raises(OutcomeUnknown, match="billable server tools"):
        usage_charge({"usage": usage}, {"input": 1, "output": 2})


@pytest.mark.parametrize("provider", ["openai", "openai-responses", "openai-chat-completions"])
def test_gateway_rejects_unimplemented_dispatch_before_using_client(provider):
    with pytest.raises(ValueError, match="Only Anthropic Messages dispatch"):
        Gateway(object(), object(), {"provider": provider})
