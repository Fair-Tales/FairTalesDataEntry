"""Provider dispatch and settlement using synthetic recorded-format fixtures.

All HTTP responses use an in-memory transport: no fixture was captured live.
"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest

from scripts.extraction_eval import gateway as gateway_module
from scripts.extraction_eval.gateway import DryRunMiss, Gateway, usage_charge
from scripts.extraction_eval.store import Ledger, OutcomeUnknown, digest


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


def test_gateway_rejects_unknown_provider_before_using_client():
    with pytest.raises(ValueError, match="provider|dispatch"):
        Gateway(object(), object(), {"provider": "unknown-provider"})


@pytest.fixture
def openai_config():
    return {
        "provider": "openai", "request_version": "1", "max_attempts": 2,
        "settings": {"extraction_model": "fixture-openai"},
        "pricing": {"verified": True, "verified_at": "fixture", "source": "fixture",
                    "models": {"fixture-openai": {
                        "input": 2, "output": 8, "cache_read": .5, "cache_write": None,
                        "max_input_tokens": 1000, "max_output_tokens": 100,
                        "max_images": 3, "max_request_bytes": 10000,
                    }}},
    }


@pytest.fixture
def openai_ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", "fixture", 1, 2, month="2026-11")
    yield ledger
    ledger.close()


@pytest.fixture
def http_fixture():
    recordings = json.loads((Path(__file__).parent / "fixtures" / "provider_gateway" /
                             "openai.json").read_text())
    return lambda name: deepcopy(recordings[name])


@pytest.fixture
def openai_transport():
    clients = []

    def create(*replies):
        pending = iter(replies)
        requests = []

        def handle(request):
            requests.append(request)
            reply = next(pending)
            if isinstance(reply, BaseException):
                raise reply
            payload = {"content": reply["raw_body"]} if "raw_body" in reply else {"json": reply["body"]}
            return httpx.Response(reply["status"], headers=reply["headers"], **payload)

        client = httpx.Client(base_url="https://api.openai.com/v1/",
                             transport=httpx.MockTransport(handle))
        clients.append(client)
        return client, requests

    yield create
    for client in clients:
        client.close()


def openai_request():
    return {"model": "fixture-openai", "max_tokens": 30,
            "messages": [{"role": "user", "content": "Extract the pages."}]}


@pytest.mark.parametrize(("provider", "fixture", "endpoint", "limit_field"), [
    ("openai", "responses", "responses", "max_output_tokens"),
    ("openai-responses", "responses", "responses", "max_output_tokens"),
    ("openai-chat-completions", "chat", "chat/completions", "max_completion_tokens"),
])
def test_openai_dispatch_persists_native_usage_before_normalizing_and_replays(
    provider, fixture, endpoint, limit_field, openai_config, openai_ledger,
    http_fixture, openai_transport, monkeypatch,
):
    openai_config["provider"] = provider
    recording = http_fixture(fixture)
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    normalize = gateway_module.normalize_usage
    normalizations = []

    def observed_normalize(usage, provider):
        row = openai_ledger.rows()[0]
        persisted = json.loads(openai_ledger.get(row["key"])["response"])
        assert persisted["usage"] == recording["body"]["usage"]
        assert persisted["_request_id"] == recording["headers"]["x-request-id"]
        assert row["state"] == "dispatched"
        normalizations.append(usage)
        return normalize(usage, provider)

    monkeypatch.setattr(gateway_module, "normalize_usage", observed_normalize)
    reply = gateway.create(**openai_request())
    assert reply.content[0].type == "text"
    assert reply.content[0].text == '{"pages": []}'
    assert reply.stop_reason == "end_turn"
    expected_key = digest({"provider": provider, "replicate": "1", "request_version": "1",
                           "request": openai_request(), "attempt": 0})
    assert reply._eval_key == expected_key
    row = openai_ledger.get(expected_key)
    assert row["cost"] == 250_000  # cached input discounted; reasoning already in output.
    assert row["reservation"] == 0
    assert row["state"] == "completed"
    assert openai_ledger.rows()[0]["stop_reason"] == "end_turn"
    assert json.loads(row["response"]) == {
        **recording["body"], "_request_id": recording["headers"]["x-request-id"],
    }
    assert requests[0].url.path == "/v1/" + endpoint
    assert requests[0].method == "POST"
    assert json.loads(requests[0].content)[limit_field] == 30

    gateway.record_usage(reply, "fixture-openai", "extraction")
    openai_config["parser_version"] = "changed-parser"
    replay = Gateway(client, openai_ledger, openai_config).create(**openai_request())
    assert replay._eval_cached is True
    assert replay._eval_key == reply._eval_key
    assert replay.content[0].text == reply.content[0].text
    assert len(requests) == len(normalizations) == len(openai_ledger.rows()) == 1
    assert openai_ledger.get(expected_key)["cost"] == row["cost"]


def test_openai_429_retries_once_with_separate_reservations_and_records(
    openai_config, openai_ledger, http_fixture, openai_transport, monkeypatch,
):
    client, requests = openai_transport(http_fixture("rate_limit"), http_fixture("responses"))
    reserved = []
    reserve = openai_ledger.reserve

    def record_reservation(key, request, ceiling):
        reserve(key, request, ceiling)
        reserved.append((key, openai_ledger.get(key)["reservation"]))

    monkeypatch.setattr(openai_ledger, "reserve", record_reservation)
    gateway = Gateway(client, openai_ledger, openai_config)
    reply = gateway.create(**openai_request())
    rows = openai_ledger.rows()
    assert len(requests) == len(rows) == len(reserved) == 2
    assert len({key for key, amount in reserved if amount > 0}) == 2
    assert [row["state"] for row in rows] == ["retryable", "completed"]
    assert [row["request_id"] for row in rows] == ["req-fixture-rate-limit", "req-fixture-responses"]
    assert sum(row["cost_usd"] for row in rows) == pytest.approx(.000250)
    assert sum(row["reserved_usd"] for row in rows) == 0
    assert gateway.create(**openai_request())._eval_key == reply._eval_key
    assert len(requests) == 2


@pytest.mark.parametrize("malformation", ["json", "envelope", "usage"])
def test_malformed_openai_response_is_retained_without_replay_or_false_settlement(
    malformation, openai_config, openai_ledger, http_fixture, openai_transport,
):
    recording = http_fixture("responses")
    if malformation == "json":
        recording = http_fixture("invalid_json")
    elif malformation == "envelope":
        recording["body"] = ["unexpected envelope"]
    else:
        recording["body"]["usage"]["input_tokens_details"]["cached_tokens"] = 101
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            gateway.create(**openai_request())
    assert len(requests) == len(openai_ledger.rows()) == 1
    row = openai_ledger.get(openai_ledger.rows()[0]["key"])
    assert row["state"] == "outcome_unknown"
    assert row["reservation"] > 0
    assert row["cost"] == 0
    assert recording["headers"]["x-request-id"] in row["response"]
    if malformation == "json":
        assert recording["raw_body"] in row["response"]
    elif malformation == "envelope":
        assert "unexpected envelope" in row["response"]
    else:
        assert json.loads(row["response"])["usage"] == recording["body"]["usage"]


def test_malformed_output_preserves_known_charge_and_does_not_redispatch(
    openai_config, openai_ledger, http_fixture, openai_transport,
):
    recording = http_fixture("responses")
    recording["body"]["output"] = "unexpected output"
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    for _ in range(2):
        with pytest.raises(ValueError):
            gateway.create(**openai_request())
    assert len(requests) == len(openai_ledger.rows()) == 1
    row = openai_ledger.get(openai_ledger.rows()[0]["key"])
    assert row["state"] == "completed"
    assert row["cost"] == 250_000
    assert row["reservation"] == 0
    assert json.loads(row["response"])["output"] == "unexpected output"


@pytest.mark.parametrize("outcome", ["timeout", "server_error", "cancel"])
def test_uncertain_openai_outcome_never_retries(
    outcome, openai_config, openai_ledger, http_fixture, openai_transport,
):
    if outcome == "timeout":
        reply = httpx.ReadTimeout("fixture response lost")
    elif outcome == "cancel":
        reply = KeyboardInterrupt("fixture cancellation")
    else:
        reply = http_fixture("rate_limit")
        reply["status"] = 500
    client, requests = openai_transport(reply)
    gateway = Gateway(client, openai_ledger, openai_config)
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            gateway.create(**openai_request())
    assert len(requests) == 1
    assert openai_ledger.rows()[0]["state"] == "outcome_unknown"
    assert openai_ledger.rows()[0]["reserved_usd"] > 0


@pytest.mark.parametrize("provider", ["openai-responses", "openai-chat-completions"])
def test_vision_pipeline_sends_images_and_rejects_truncated_json(
    provider, openai_config, openai_ledger, http_fixture, openai_transport, monkeypatch,
):
    import utilities

    openai_config["provider"] = provider
    chat = provider == "openai-chat-completions"
    recording = http_fixture("chat" if chat else "responses")
    if chat:
        recording["body"]["choices"][0]["finish_reason"] = "length"
    else:
        recording["body"]["status"] = "incomplete"
        recording["body"]["incomplete_details"] = {"reason": "max_output_tokens"}
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    monkeypatch.setattr(utilities, "_log_usage", gateway.record_usage)
    data, raw = utilities.vision_json(gateway, [b"fixture image"], "Extract the pages.",
                                     model="fixture-openai", max_tokens=30,
                                     downscale=False, reject_partial=True)
    assert data is None
    assert raw.failure_kind == "truncated_response"
    payload = json.loads(requests[0].content)
    blocks = payload["messages" if chat else "input"][0]["content"]
    image = blocks[0]
    assert image["type"] == ("image_url" if chat else "input_image")
    assert (image["image_url"]["url"] if chat else image["image_url"]) == (
        "data:image/jpeg;base64,Zml4dHVyZSBpbWFnZQ==")
    assert blocks[1] == {"type": "text" if chat else "input_text", "text": "Extract the pages."}
    assert openai_ledger.rows()[0]["state"] == "completed"
    assert openai_ledger.rows()[0]["stop_reason"] == "max_tokens"


def test_openai_refusal_is_retained_and_not_parsed_as_extraction(
    openai_config, openai_ledger, http_fixture, openai_transport, monkeypatch,
):
    import utilities

    recording = http_fixture("responses")
    recording["body"]["output"][1]["content"] = [
        {"type": "refusal", "refusal": "Fixture refusal."}]
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    monkeypatch.setattr(utilities, "_log_usage", gateway.record_usage)
    data, raw = utilities.vision_json(gateway, [], "Extract the pages.",
                                     model="fixture-openai", max_tokens=30,
                                     reject_partial=True)
    assert data is None
    assert not raw
    assert len(requests) == 1
    row = openai_ledger.get(openai_ledger.rows()[0]["key"])
    assert "Fixture refusal." in row["response"]
    assert row["state"] == "completed"
    assert openai_ledger.rows()[0]["stop_reason"] == "refusal"


@pytest.mark.parametrize("option", [{"tools": []}, {"top_k": 2}])
def test_unsupported_openai_options_fail_before_reservation(
    option, openai_config, openai_ledger, openai_transport,
):
    client, requests = openai_transport()
    gateway = Gateway(client, openai_ledger, openai_config)
    with pytest.raises(ValueError, match="options"):
        gateway.create(**openai_request(), **option)
    assert openai_ledger.rows() == requests == []


def test_absent_openai_client_is_clear_configuration_error(openai_config, openai_ledger):
    with pytest.raises(ValueError, match="(?i)openai.*client.*key"):
        Gateway(None, openai_ledger, openai_config).create(**openai_request())
    assert openai_ledger.rows() == []
    with pytest.raises(DryRunMiss):
        Gateway(None, openai_ledger, openai_config, dry_run=True).create(**openai_request())
    assert openai_ledger.rows() == []
