"""Provider dispatch and settlement using synthetic recorded-format fixtures.

All HTTP responses use an in-memory transport: no fixture was captured live.
"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from scripts.extraction_eval.billing import cycle_budgets
from scripts.extraction_eval import gateway as gateway_module
from scripts.extraction_eval.gateway import DryRunMiss, Gateway, RequestLimitError, usage_charge
from scripts.extraction_eval.openai_adapter import canonical_request, prepare_request
from scripts.extraction_eval.outcomes import EvaluationFailure
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
                          "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 0,
            "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0}},
                          "output_tokens_details": {"reasoning_tokens": 4}}),
    ("openai-chat-completions", {"prompt_tokens": 100, "completion_tokens": 10,
                                "prompt_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 0},
                                "completion_tokens_details": {"reasoning_tokens": 4}}),
])
def test_openai_settlement_subtracts_cached_input_once(provider, usage):
    rate = {"input": 2, "output": 8, "cache_read": .5, "cache_write": 2.5, "cache_ttl": "30m"}
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
    rate = {"input": 2, "output": 8, "cache_read": .5, "cache_write": 2.5, "cache_ttl": "30m"}
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
        "billing_cycles": {"anthropic": {"start_day": 1}, "openai": {"start_day": 10}},
        "provider": "openai", "request_version": "1", "max_attempts": 2,
        "settings": {"extraction_model": "gpt-6.1-sol"},
        "pricing": {"verified": True, "verified_at": "fixture", "source": "fixture",
                    "models": {"gpt-6.1-sol": {
                        "input": 2, "output": 8, "cache_read": .5, "cache_write": 2.5, "cache_ttl": "30m",
                        "max_input_tokens": 1000, "max_output_tokens": 128000,
                        "max_images": 3, "max_request_bytes": 10000,
                    }}},
    }


@pytest.fixture
def openai_ledger(tmp_path, openai_config):
    ledger = Ledger(tmp_path / "ledger.sqlite", "fixture", 1, budgets=cycle_budgets(openai_config, 2))
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
    return {"model": "gpt-6.1-sol", "max_tokens": 30,
            "messages": [{"role": "user", "content": "Extract the pages."}]}


@pytest.mark.parametrize(("provider", "fixture", "endpoint", "limit_field"), [
    ("openai", "responses", "responses", "max_output_tokens"),
    ("openai-responses", "responses", "responses", "max_output_tokens"),
    ("openai-chat-completions", "chat", "chat/completions", "max_completion_tokens"),
])
@pytest.mark.parametrize("controls", [{}, {"store": False, "background": False}])
def test_openai_dispatch_persists_native_usage_before_normalizing_and_replays(
    provider, fixture, endpoint, limit_field, controls, openai_config, openai_ledger,
    http_fixture, openai_transport, monkeypatch,
):
    openai_config["provider"] = provider
    recording = http_fixture(fixture)
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    normalize = gateway_module.normalize_usage
    normalizations = []

    def observed_normalize(usage, provider, **kwargs):
        row = openai_ledger.rows()[0]
        persisted = json.loads(openai_ledger.get(row["key"])["response"])
        assert persisted["usage"] == recording["body"]["usage"]
        assert persisted["_request_id"] == recording["headers"]["x-request-id"]
        assert row["state"] == "dispatched"
        normalizations.append(usage)
        return normalize(usage, provider, **kwargs)

    monkeypatch.setattr(gateway_module, "normalize_usage", observed_normalize)
    reply = gateway.create(**openai_request(), **controls)
    assert reply.content[0].type == "text"
    assert reply.content[0].text == '{"pages": []}'
    assert reply.stop_reason == "end_turn"
    expected_key = digest({"provider": provider, "replicate": "1", "request_version": "1",
                           "request": canonical_request(openai_request(), provider), "attempt": 0})
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
    assert json.loads(requests[0].content)[limit_field] == 25000
    assert json.loads(requests[0].content)["store"] is False
    assert json.loads(requests[0].content).get("background", False) is False

    gateway.record_usage(reply, "gpt-6.1-sol", "extraction")
    openai_config["parser_version"] = "changed-parser"
    replay = Gateway(client, openai_ledger, openai_config).create(**openai_request())
    assert replay._eval_cached is True
    assert replay._eval_key == reply._eval_key
    assert replay.content[0].text == reply.content[0].text
    assert len(requests) == len(normalizations) == len(openai_ledger.rows()) == 1
    assert openai_ledger.get(expected_key)["cost"] == row["cost"]


@pytest.mark.parametrize('status', [429, 500, 502, 503, 529])
def test_openai_retries_with_delay_and_separate_reservations_and_records(
    openai_config, openai_ledger, http_fixture, openai_transport, monkeypatch,
    status,
):
    rejection = http_fixture('rate_limit')
    rejection['status'] = status
    rejection['headers']['retry-after'] = '7'
    client, requests = openai_transport(rejection, http_fixture("responses"))
    reserved = []
    reserve = openai_ledger.reserve

    def record_reservation(key, request, ceiling):
        reserve(key, request, ceiling)
        reserved.append((key, openai_ledger.get(key)["reservation"]))

    monkeypatch.setattr(openai_ledger, "reserve", record_reservation)
    waits = []
    def wait(seconds):
        assert len(requests) == len(reserved) == 1
        waits.append(seconds)
    gateway = Gateway(client, openai_ledger, openai_config, wait=wait)
    reply = gateway.create(**openai_request())
    rows = openai_ledger.rows()
    assert len(requests) == len(rows) == len(reserved) == 2
    assert all(json.loads(request.content)["store"] is False for request in requests)
    assert len({key for key, amount in reserved if amount > 0}) == 2
    assert [row["state"] for row in rows] == ["retryable", "completed"]
    assert [row["request_id"] for row in rows] == ["req-fixture-rate-limit", "req-fixture-responses"]
    assert sum(row["cost_usd"] for row in rows) == pytest.approx(.000250)
    assert sum(row["reserved_usd"] for row in rows) > 0
    assert gateway.create(**openai_request())._eval_key == reply._eval_key
    assert len(requests) == 2
    assert waits == [7]


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
    assert row["state"] == "failed"
    assert row["cost"] == 250_000
    assert row["reservation"] == 0
    assert json.loads(row["response"])["output"] == "unexpected output"


@pytest.mark.parametrize("outcome", ["timeout", "cancel"])
def test_uncertain_openai_outcome_never_retries(
    outcome, openai_config, openai_ledger, http_fixture, openai_transport,
):
    if outcome == "timeout":
        reply = httpx.ReadTimeout("fixture response lost")
    elif outcome == "cancel":
        reply = KeyboardInterrupt("fixture cancellation")
    client, requests = openai_transport(reply)
    gateway = Gateway(client, openai_ledger, openai_config)
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            gateway.create(**openai_request())
    assert len(requests) == 1
    assert openai_ledger.rows()[0]["state"] == "outcome_unknown"
    assert openai_ledger.rows()[0]["reserved_usd"] > 0
    result = openai_ledger.retry_failed()
    assert result['keys'] == []
    assert result['unknown'] == [openai_ledger.rows()[0]['key']]
    with pytest.raises(OutcomeUnknown):
        Gateway(client, openai_ledger, openai_config).create(**openai_request())
    assert len(requests) == 1


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
    with pytest.raises(EvaluationFailure) as error:
        utilities.vision_json(gateway, [b"fixture image"], "Extract the pages.",
                              model="gpt-6.1-sol", max_tokens=30,
                              downscale=False, reject_partial=True)
    assert error.value.failure_kind == "truncated"
    payload = json.loads(requests[0].content)
    blocks = payload["messages" if chat else "input"][0]["content"]
    image = blocks[0]
    assert image["type"] == ("image_url" if chat else "input_image")
    assert (image["image_url"]["url"] if chat else image["image_url"]) == (
        "data:image/jpeg;base64,Zml4dHVyZSBpbWFnZQ==")
    assert blocks[1] == {"type": "text" if chat else "input_text", "text": "Extract the pages."}
    assert openai_ledger.rows()[0]["state"] == "failed"
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
    with pytest.raises(EvaluationFailure) as error:
        utilities.vision_json(gateway, [], "Extract the pages.",
                              model="gpt-6.1-sol", max_tokens=30, reject_partial=True)
    assert error.value.failure_kind == "refusal"
    assert len(requests) == 1
    row = openai_ledger.get(openai_ledger.rows()[0]["key"])
    assert "Fixture refusal." in row["response"]
    assert row["state"] == "failed"
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


@pytest.mark.parametrize("provider", ["openai", "openai-responses", "openai-chat-completions"])
@pytest.mark.parametrize("option", ["store", "background"])
def test_openai_storage_and_background_refused_before_reservation(
    provider, option, openai_config, openai_ledger, openai_transport, monkeypatch,
):
    openai_config["provider"] = provider
    client, requests = openai_transport()
    reserve = Mock(side_effect=AssertionError("Must refuse before reservation"))
    monkeypatch.setattr(openai_ledger, "reserve", reserve)
    request = {**openai_request(), option: True}
    with pytest.raises(ValueError, match="store=false and foreground"):
        prepare_request(request, provider)
    for dry_run in (False, True):
        gateway = Gateway(client, openai_ledger, openai_config, dry_run=dry_run)
        with pytest.raises(RequestLimitError, match="store=false and foreground"):
            gateway.create(**request)
    reserve.assert_not_called()
    assert openai_ledger.rows() == requests == []


def test_anthropic_dispatch_and_cache_identity_unchanged(openai_config, openai_ledger):
    openai_config["provider"] = "anthropic-messages"
    openai_config["pricing"]["models"]["gpt-6.1-sol"]["cache_write"] = 2.5
    client = Mock()
    client.messages.create.return_value = {
        "content": [{"type": "text", "text": "Synthetic reply"}],
        "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    gateway = Gateway(client, openai_ledger, openai_config)
    request = openai_request()
    reply = gateway.create(**request)
    client.messages.create.assert_called_once_with(**request)
    assert reply._eval_key == digest({
        "provider": "anthropic-messages", "replicate": "1", "request_version": "1",
        "request": request, "attempt": 0,
    })
    assert gateway.create(**request)._eval_cached is True
    client.messages.create.assert_called_once_with(**request)


def test_absent_openai_client_is_clear_configuration_error(openai_config, openai_ledger):
    with pytest.raises(ValueError, match="(?i)openai.*client.*key"):
        Gateway(None, openai_ledger, openai_config).create(**openai_request())
    assert openai_ledger.rows() == []
    with pytest.raises(DryRunMiss):
        Gateway(None, openai_ledger, openai_config, dry_run=True).create(**openai_request())
    assert openai_ledger.rows() == []


@pytest.mark.parametrize('provider,fixture', [
    ('openai-responses', 'responses'), ('openai-chat-completions', 'chat')])
@pytest.mark.parametrize('outcome,kind', [
    ('empty', 'invalid_response'), ('whitespace', 'invalid_response'),
    ('null', 'invalid_response'), ('refusal', 'refusal'),
    ('length', 'truncated'), ('length_empty', 'truncated'),
    ('filter', 'output_blocked'), ('malformed', 'invalid_response')])
def test_charged_failure_is_atomic_and_replayed_as_same_failure(
        provider, fixture, outcome, kind, openai_config, openai_ledger,
        http_fixture, openai_transport, monkeypatch):
    openai_config['provider'] = provider
    recording = http_fixture(fixture)
    raw = recording['body']
    text = {'empty': '', 'whitespace': ' \n', 'null': None, 'length_empty': ''}.get(outcome, '{}')
    if fixture == 'chat':
        choice = raw['choices'][0]
        choice['message']['content'] = text
        if outcome == 'refusal':
            choice['message']['refusal'] = 'Fixture refusal'
        if outcome.startswith('length'):
            choice['finish_reason'] = 'length'
        if outcome == 'filter':
            choice['finish_reason'] = 'content_filter'
        if outcome == 'malformed':
            raw['choices'] = []
    else:
        raw['output'][1]['content'][0]['text'] = text
        raw['output'][1]['content'] = raw['output'][1]['content'][:1]
        if outcome == 'null':
            raw['output'] = raw['output'][:1]  # reasoning only
        if outcome == 'refusal':
            raw['output'][1]['content'] = [{'type': 'refusal', 'refusal': 'Fixture refusal'}]
        if outcome.startswith('length') or outcome == 'filter':
            raw['status'] = 'incomplete'
            raw['incomplete_details'] = {'reason': 'content_filter' if outcome == 'filter' else 'max_output_tokens'}
        if outcome == 'malformed':
            raw['output'] = 'invalid'
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    settle = openai_ledger.settle
    def observed_settle(*args, **kwargs):
        assert kwargs['failure'].failure_kind == kind
        settle(*args, **kwargs)
        assert openai_ledger.rows()[0]['state'] == 'failed'
    monkeypatch.setattr(openai_ledger, 'settle', observed_settle)
    for current in (gateway, Gateway(None, openai_ledger, openai_config, dry_run=True)):
        with pytest.raises(EvaluationFailure) as error:
            current.create(**openai_request())
        assert error.value.failure_kind == kind
        assert error.value.raw_response['usage'] == raw['usage']
        assert current.cache_hits == 0
    row = openai_ledger.get(openai_ledger.rows()[0]['key'])
    assert row['state'] == 'failed'
    assert openai_ledger.rows()[0]['failure_kind'] == kind
    assert row['cost'] == 250000
    assert row['reservation'] == 0
    assert row['error'].startswith(kind + ':')
    assert json.loads(row['response']) == {**raw, '_request_id': recording['headers']['x-request-id']}
    assert len(requests) == 1


@pytest.mark.parametrize('fixture,provider', [
    ('responses', 'openai-responses'), ('chat', 'openai-chat-completions')])
def test_explicit_wordless_page_remains_success(
        fixture, provider, openai_config, openai_ledger, http_fixture, openai_transport, monkeypatch):
    import utilities
    from extraction_core import attempt_page_extraction
    from pipeline_config import validate_settings
    page = {'has_text': False, 'text': '', 'is_story_page': True, 'page_type': 'wordless_narrative'}
    recording = http_fixture(fixture)
    if fixture == 'chat':
        recording['body']['choices'][0]['message']['content'] = json.dumps(page)
    else:
        recording['body']['output'][1]['content'] = [{'type': 'output_text', 'text': json.dumps(page)}]
    openai_config['provider'] = provider
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    monkeypatch.setattr(utilities, '_log_usage', gateway.record_usage)
    monkeypatch.setattr(utilities, 'build_vision_content', lambda *a, **kw: 'Synthetic image')
    settings = validate_settings({'extraction_model': 'gpt-6.1-sol'}, model_ids=['gpt-6.1-sol'])
    status, result = attempt_page_extraction(b'image', gateway, settings, label='page_extraction')
    assert status == 'ok'
    assert result[0] == ''
    assert openai_ledger.rows()[0]['state'] == 'completed'
    assert len(requests) == 1


@pytest.mark.parametrize('provider,fixture,change', [
    (provider, fixture, change)
    for provider, fixture in [('openai-responses', 'responses'), ('openai-chat-completions', 'chat')]
    for change in (['missing_write', 'null_write', 'bad_write', 'overlap']
                   + (['missing_split', 'bad_sum', 'wrong_ttl'] if fixture == 'responses' else []))])
def test_unknown_write_usage_stops_other_requests_even_after_gateway_restart(
        provider, fixture, change, openai_config, openai_ledger, http_fixture, openai_transport):
    recording = http_fixture(fixture)
    details = recording['body']['usage']['prompt_tokens_details' if fixture == 'chat' else 'input_tokens_details']
    if change == 'missing_write':
        details.pop('cache_write_tokens')
    elif change == 'null_write':
        details['cache_write_tokens'] = None
    elif change == 'bad_write':
        details['cache_write_tokens'] = '0'
    elif change == 'overlap':
        details['cached_tokens'] = 101
    elif change == 'missing_split':
        details.pop('cache_write_tokens_details')
    elif change == 'bad_sum':
        details['cache_write_tokens_details']['ttl_30m_tokens'] = 1
    else:
        details['cache_write_tokens'] = 1
        details['cache_write_tokens_details']['ttl_12h_tokens'] = 1
    openai_config['provider'] = provider
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    with pytest.raises(OutcomeUnknown):
        gateway.create(**openai_request())
    for current in (gateway, Gateway(client, openai_ledger, openai_config)):
        different = {**openai_request(), 'messages': [{'role': 'user', 'content': 'Different request'}]}
        with pytest.raises(OutcomeUnknown):
            current.create(**different)
    row = openai_ledger.rows()[0]
    assert row['state'] == 'outcome_unknown'
    assert row['reserved_usd'] > 0
    assert row['cost_usd'] == 0  # reservation remains; no settled/free success
    assert len(requests) == len(openai_ledger.rows()) == 1


@pytest.mark.parametrize('change', ['effort', 'floor', 'lifetime'])
def test_openai_identity_includes_effort_floor_even_below_cap_and_lifetime(
        change, openai_config, openai_ledger, http_fixture, openai_transport):
    client, requests = openai_transport(http_fixture('responses'))
    request = {**openai_request(), 'max_tokens': 30000}
    gateway = Gateway(client, openai_ledger, openai_config)
    response = gateway.create(**request)
    changed = deepcopy(request)
    if change == 'effort':
        changed['reasoning'] = {'effort': 'medium'}
    elif change == 'floor':
        changed['openai_output_floor'] = 26000
    else:
        changed['prompt_cache_options'] = {'ttl': '12h'}
        openai_config['pricing']['models']['gpt-6.1-sol'].update(cache_ttl='12h', cache_write=5)
    dry = Gateway(None, openai_ledger, openai_config, dry_run=True)
    with pytest.raises(DryRunMiss):
        dry.create(**changed)
    assert response._eval_cached is False
    assert len(requests) == 1


@pytest.mark.parametrize('provider,fixture', [
    ('openai-responses', 'responses'), ('openai-chat-completions', 'chat')])
def test_rotation_shared_settings_reach_payload_and_return_valid_label(
        provider, fixture, openai_config, openai_ledger, http_fixture, openai_transport, monkeypatch):
    import utilities
    from image_processing import get_rotation_angle
    from pipeline_config import validate_settings
    recording = http_fixture(fixture)
    if fixture == 'chat':
        recording['body']['choices'][0]['message']['content'] = 'UPRIGHT'
    else:
        recording['body']['output'][1]['content'] = [{'type': 'output_text', 'text': 'UPRIGHT'}]
    openai_config['provider'] = provider
    client, requests = openai_transport(recording)
    gateway = Gateway(client, openai_ledger, openai_config)
    monkeypatch.setattr(utilities, '_log_usage', gateway.record_usage)
    monkeypatch.setattr(utilities, 'build_vision_content', lambda *a, **kw: 'Synthetic image')
    settings = validate_settings({'rotation_model': 'gpt-6.1-sol',
                                  'rotation_openai_reasoning_effort': 'low',
                                  'rotation_openai_output_floor': 26000}, model_ids=['gpt-6.1-sol'])
    assert get_rotation_angle(b'image', gateway, settings=settings) == (0, False)
    payload = json.loads(requests[0].content)
    assert payload['reasoning_effort' if fixture == 'chat' else 'reasoning'] == (
        'low' if fixture == 'chat' else {'effort': 'low'})
    assert payload['max_completion_tokens' if fixture == 'chat' else 'max_output_tokens'] == 26000
    assert payload['prompt_cache_options'] == {'ttl': '30m'}
    assert 'openai_output_floor' not in payload


@pytest.mark.parametrize('price_change', [
    {'cache_write': None}, {'cache_write': 0}, {'cache_write': 5},
    {'cache_ttl': None}, {'cache_ttl': '24h'}, {'max_input_tokens': 272001}])
def test_unknown_write_tariff_or_lifetime_prevents_dispatch(
        price_change, openai_config, openai_ledger, openai_transport):
    openai_config['pricing']['models']['gpt-6.1-sol'].update(price_change)
    client, requests = openai_transport()
    with pytest.raises(ValueError):
        Gateway(client, openai_ledger, openai_config).create(**openai_request())
    assert requests == openai_ledger.rows() == []


@pytest.mark.parametrize('options', [
    {'reasoning': {'effort': 'none'}}, {'reasoning': {'effort': 'minimal'}},
    {'reasoning': {'effort': 'low', 'mode': 'pro'}}, {'reasoning': None},
    {'openai_output_floor': True}, {'max_tokens': True},
    {'model': 'gpt-6.1-sol-unverified'}, {'prompt_cache_options': {'ttl': '24h'}},
    {'prompt_cache_options': {'ttl': '30m', 'mode': 'explicit'}},
    {'thinking': {'type': 'disabled'}}, {'output_config': {'effort': 'low'}},
])
def test_bad_generation_settings_never_reserve(
        options, openai_config, openai_ledger, openai_transport):
    client, requests = openai_transport()
    with pytest.raises(RequestLimitError):
        Gateway(client, openai_ledger, openai_config).create(**{**openai_request(), **options})
    assert requests == openai_ledger.rows() == []


def test_legacy_completed_empty_reply_is_not_replayed_as_success(
        openai_config, openai_ledger, http_fixture, openai_transport):
    recording = http_fixture('responses')
    recording['body']['output'] = []
    request = canonical_request(openai_request(), 'openai')
    identity = {'provider': 'openai', 'replicate': '1', 'request_version': '1', 'request': request}
    key = digest({**identity, 'attempt': 0})
    openai_ledger.reserve(key, identity, Decimal('.001'))
    openai_ledger.settle(key, recording['body'], Decimal('.000250'))
    client, requests = openai_transport()
    gateway = Gateway(client, openai_ledger, openai_config)
    with pytest.raises(EvaluationFailure) as error:
        gateway.create(**openai_request())
    assert error.value.failure_kind == 'invalid_response'
    assert openai_ledger.get(key)['state'] == 'failed'
    assert openai_ledger.get(key)['cost'] == 250000
    assert requests == []
    assert gateway.cache_hits == 0
