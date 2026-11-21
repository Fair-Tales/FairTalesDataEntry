"""A9: request identity and response provenance, entirely synthetic/offline."""
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_pricing import PRICING_PER_MTOK
from ai_runtime import extraction_runtime
from pipeline_config import (
    STAGE_MAX_TOKENS, OPENAI_REASONING_EFFORTS, generation_request, model_options, requested_generation,
    validate_generation_request, validate_settings,
)
from scripts.extraction_eval.billing import cycle_budgets
from scripts.extraction_eval.gateway import Gateway, DryRunMiss, RequestLimitError, request_ceiling, validate_prices
from scripts.extraction_eval.store import Ledger


@pytest.mark.parametrize('model,default_effort,rates', [
    ('claude-sonnet-5-5', 'high', (2, 10, .1, 2.5)),
    ('claude-opus-5-5', 'medium', (4, 20, .2, 5)),
    ('claude-fable-5-2', 'high', (8, 40, .2, 10)),
])
def test_new_claude_models_from_stage_settings_to_priced_request(model, default_effort, rates):
    supported = ('extraction', 'metadata', 'story_context', 'character_detection', 'human_vision', 'theme')
    settings = validate_settings({f'{stage}_model': model for stage in supported})
    config = json.loads(Path('scripts/extraction_eval/baseline.json').read_text())
    prices = validate_prices({**config, 'settings': settings}, executing=True)
    for field, rate in zip(('input', 'output', 'cache_read', 'cache_write'), rates):
        assert prices[model][field] == PRICING_PER_MTOK[model][field] == rate
    for stage in supported:
        cap = STAGE_MAX_TOKENS[stage]
        request = generation_request(settings, stage)
        assert request == {'model': model, 'max_tokens': cap}
        assert requested_generation(request)['documented_defaults'] == {
            'thinking': 'adaptive', 'effort': default_effort}
        validate_generation_request(request)
        assert request_ceiling(request, prices) == Decimal(rates[3]) + Decimal(cap * rates[1]) / 1_000_000

    for effort in ('low', 'medium', 'high', 'xhigh', 'max'):
        request = generation_request({**settings, 'metadata_thinking': 'adaptive',
                                      'metadata_effort': effort}, 'metadata')
        validate_generation_request(request)
    for options in ({'thinking': {'type': 'disabled'}},
                    {'thinking': {'type': 'enabled', 'budget_tokens': 1024}},
                    {'temperature': 1}, {'top_p': 1}, {'top_k': 1}):
        with pytest.raises(ValueError):
            validate_generation_request({**request, **options})


@pytest.mark.parametrize('key', ['locate_model', 'rotation_model', 'crop_quality_model'])
def test_small_output_model_choices_match_validation(key):
    for model in ('claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-2'):
        assert model not in model_options(key)
        with pytest.raises(ValueError, match=key):
            validate_settings({key: model}, model_ids=[model])


@pytest.mark.parametrize('model', ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-2'])
def test_between_tools_requires_sonnet_55_and_at_most_high_effort(model):
    for effort in ('default', 'low', 'medium', 'high', 'xhigh', 'max'):
        overrides = dict(metadata_model=model, metadata_thinking='between_tools', metadata_effort=effort)
        if model == 'claude-sonnet-5-5' and effort not in ('xhigh', 'max'):
            request = generation_request(validate_settings(overrides), 'metadata')
            assert request['thinking'] == {'type': 'between_tools'}
            validate_generation_request(request)
        else:
            with pytest.raises(ValueError):
                validate_settings(overrides)


@pytest.mark.parametrize('stage', STAGE_MAX_TOKENS)
@pytest.mark.parametrize('model', OPENAI_REASONING_EFFORTS)
def test_each_stage_has_validated_openai_settings_and_output_floor(stage, model):
    for effort in OPENAI_REASONING_EFFORTS[model]:
        settings = validate_settings({f'{stage}_model': model,
                                      f'{stage}_openai_reasoning_effort': effort}, model_ids=[model])
        request = generation_request(settings, stage)
        assert request['reasoning'] == {'effort': effort}
        assert request['openai_output_floor'] == request['max_tokens'] == 25000
    for effort in ('minimal', 'ultra', 'default', None, True):
        with pytest.raises(ValueError):
            validate_settings({f'{stage}_model': model,
                               f'{stage}_openai_reasoning_effort': effort}, model_ids=[model])


@pytest.mark.parametrize('model', ['gpt-6-astra', 'gpt-6.1-sol'])
def test_none_effort_rejected_on_models_that_require_reasoning(model):
    with pytest.raises(ValueError):
        validate_settings({'rotation_model': model, 'rotation_openai_reasoning_effort': 'none'},
                          model_ids=[model])


@pytest.mark.parametrize('floor', [0, 16, 255, True, 25000.0, 128001, None])
def test_invalid_openai_floor(floor):
    with pytest.raises(ValueError):
        validate_settings({'rotation_openai_output_floor': floor})


@pytest.fixture
def setup_gateway(tmp_path):
    config = json.loads(Path('scripts/extraction_eval/baseline.json').read_text())
    config['billing_cycles'] = {'anthropic': {'start_day': 1}}
    for limits in config['pricing']['models'].values():
        limits['max_output_tokens'] = 20000
    client = SimpleNamespace(messages=Mock())
    client.messages.create.return_value = {
        'model': 'provider-served-revision', 'stop_reason': 'end_turn',
        'content': [{'type': 'text', 'text': '{}'}],
        'usage': {'input_tokens': 7, 'output_tokens': 3},
    }
    ledger = Ledger(tmp_path / 'spend.sqlite', 'test', 100,
                    budgets=cycle_budgets(config, 100))
    yield client, ledger, config
    ledger.close()


def body(settings, stage):
    return {**generation_request(settings, stage),
            'messages': [{'role': 'user', 'content': stage}]}


@pytest.mark.parametrize('stage,tokens', STAGE_MAX_TOKENS.items())
def test_defaults_add_no_body_options(stage, tokens):
    settings = validate_settings({})
    assert generation_request(settings, stage) == {
        'model': settings[f'{stage}_model'], 'max_tokens': tokens}
    assert settings[f'{stage}_thinking'] == settings[f'{stage}_effort'] == 'default'


@pytest.mark.parametrize('stage,tokens', [
    ('metadata', 20000), ('character_detection', 20000),
    ('story_context', 20000), ('extraction', 8192),
])
def test_default_stage_call_carries_thinking_headroom(monkeypatch, stage, tokens):
    import extraction_core
    import utilities

    client = SimpleNamespace(messages=Mock())
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type='text', text='{"characters": []}')],
        stop_reason='end_turn')
    monkeypatch.setattr(utilities, 'build_vision_content', lambda *a, **k: [])
    settings = validate_settings({'story_context_limits': {
        'max_images': 100, 'max_request_bytes': 32000000,
        'max_input_tokens': 200000, 'max_output_tokens': 20000,
    }})
    if stage == 'story_context':
        # Context is opt-in and has no legacy prompt.
        settings['prompt_variant'] = 'rules-plus-icl'
    with extraction_runtime(client=client, settings=settings, usage_sink=lambda *a: None):
        if stage == 'metadata':
            utilities.extract_joint_metadata([(1, b'image')], client)
        elif stage == 'character_detection':
            utilities.detect_book_characters([(1, 'Mia waved.')], client)
        elif stage == 'story_context':
            extraction_core.classify_book_context(
                [{'page_number': 1, 'text': 'Mia waved.'}], [(1, b'image')],
                client, settings, lambda *a: None)
        else:
            extraction_core.attempt_page_extraction(
                b'image', client, settings, label='page_extraction')
    client.messages.create.assert_called_once()
    request = client.messages.create.call_args.kwargs
    assert request['max_tokens'] == tokens
    assert 'thinking' not in request
    assert 'output_config' not in request


@pytest.mark.parametrize('stage', STAGE_MAX_TOKENS)
def test_settings_refuse_above_nonstreaming_bound(stage):
    assert validate_settings({f'{stage}_max_tokens': 20000})[f'{stage}_max_tokens'] == 20000
    with pytest.raises(ValueError):
        validate_settings({f'{stage}_max_tokens': 20001})


@pytest.mark.parametrize('stage', STAGE_MAX_TOKENS)
@pytest.mark.parametrize('field,value', [
    ('model', 'claude-sonnet-4-6'), ('max_tokens', 1024),
    ('thinking', 'disabled'), ('effort', 'low'),
])
def test_only_changed_stage_misses(stage, field, value, setup_gateway):
    client, ledger, config = setup_gateway
    settings = validate_settings({f'{name}_model': 'claude-sonnet-5' for name in STAGE_MAX_TOKENS})
    gateway = Gateway(client, ledger, config)
    for name in STAGE_MAX_TOKENS:
        gateway.context = {'stage': name}
        gateway.create(**body(settings, name))
    changed = validate_settings({**settings, f'{stage}_{field}': value})
    dry = Gateway(None, ledger, config, dry_run=True)
    for name in STAGE_MAX_TOKENS:
        if name == stage:
            with pytest.raises(DryRunMiss):
                dry.create(**body(changed, name))
        else:
            dry.create(**body(changed, name))
    assert dry.cache_hits == len(STAGE_MAX_TOKENS) - 1
    assert dry.paid_calls == 0
    assert client.messages.create.call_count == len(STAGE_MAX_TOKENS)


@pytest.mark.parametrize('overrides', [{}, {'extraction_effort': 'low', 'extraction_thinking': 'disabled'}])
def test_served_model_usage_stop_and_requested_settings_survive_reopen(overrides, setup_gateway):
    client, ledger, config = setup_gateway
    request = body(validate_settings(overrides), 'extraction')
    gateway = Gateway(client, ledger, config)
    result = gateway.create(**request)
    client.messages.create.assert_called_once_with(**request)
    row = ledger.rows()[0]
    assert row['model'] == 'claude-sonnet-5'
    assert row['served_model'] == 'provider-served-revision'
    assert row['usage'] == {'input_tokens': 7, 'output_tokens': 3}
    assert row['stop_reason'] == 'end_turn'
    assert row['requested_settings'] == requested_generation(request)
    assert row['requested_settings']['documented_defaults'] == {'thinking': 'adaptive', 'effort': 'high'}
    raw = json.loads(ledger.get(result._eval_key)['response'])
    assert raw['model'] == row['served_model']
    path = ledger.db.execute('PRAGMA database_list').fetchone()['file']
    other = Ledger(path, 'test', 100, budgets=cycle_budgets(config, 100))
    try:
        replay = Gateway(None, other, config, dry_run=True).create(**request)
        assert replay.model == row['served_model']
        assert other.rows() == ledger.rows()
    finally:
        other.close()


def test_old_rows_keep_unknown_settings_and_served_model(setup_gateway):
    client, ledger, config = setup_gateway
    gateway = Gateway(client, ledger, config)
    result = gateway.create(**body(validate_settings({}), 'extraction'))
    saved = json.loads(ledger.get(result._eval_key)['request'])
    saved.pop('context')
    with ledger.transaction():
        ledger.db.execute('UPDATE calls SET request=? WHERE key=?', (json.dumps(saved), result._eval_key))
    row = ledger.rows()[0]
    assert row['requested_settings'] is None
    assert row['served_model'] == 'provider-served-revision'
    assert Gateway(None, ledger, config, dry_run=True).create(**saved['request'])._eval_cached


@pytest.mark.parametrize('overrides', [
    {'extraction_thinking': 'enabled'},
    {'extraction_model': 'claude-haiku-4-5', 'extraction_thinking': 'adaptive'},
    {'extraction_model': 'claude-haiku-4-5', 'extraction_effort': 'low'},
    {'rotation_effort': 'xhigh'},
    {'extraction_effort': True}, {'rotation_max_tokens': False},
    {'locate_max_tokens': 0}, {'extraction_max_tokens': 20001},
])
def test_invalid_settings_rejected(overrides):
    with pytest.raises(ValueError):
        validate_settings(overrides)


@pytest.mark.parametrize('options', [
    {'thinking': {'type': 'enabled', 'budget_tokens': 1024}},
    {'model': 'claude-haiku-4-5', 'thinking': {'type': 'adaptive'}},
    {'model': 'claude-haiku-4-5', 'output_config': {'effort': 'low'}},
    {'model': 'unknown', 'output_config': {'effort': 'low'}},
    {'thinking': None}, {'thinking': {}}, {'output_config': {'format': {}}},
    {'thinking': {'type': 'default'}}, {'output_config': {'effort': 'default'}},
    {'max_tokens': True}, {'max_tokens': 20001},
    {'output_config': {'effort': 'made-up'}}, {'temperature': 0},
])
def test_invalid_body_refused_before_reservation(options, setup_gateway):
    client, ledger, config = setup_gateway
    ledger.reserve = Mock(side_effect=AssertionError('must not reserve'))
    with pytest.raises(RequestLimitError):
        Gateway(client, ledger, config).create(**{**body(validate_settings({}), 'extraction'), **options})
    ledger.reserve.assert_not_called()
    client.messages.create.assert_not_called()
    assert ledger.rows() == []


def test_unknown_model_defaults_only_and_no_openai_reasoning_mapping():
    settings = validate_settings({'extraction_model': 'future-model'}, model_ids=['future-model'])
    assert generation_request(settings, 'extraction') == {'model': 'future-model', 'max_tokens': 8192}
    with pytest.raises(ValueError):
        validate_settings({**settings, 'extraction_effort': 'low'}, model_ids=['future-model'])


def test_vision_and_text_helpers_forward_nondefault_settings(monkeypatch):
    import utilities
    from image_processing import get_rotation_angle, check_crop_quality
    client = SimpleNamespace(messages=Mock())
    requests = []
    replies = iter(['UPRIGHT', 'YES', '{"characters": []}', '{}', '{}'])
    def create(**request):
        requests.append(request)
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=next(replies))], stop_reason='end_turn')
    client.messages.create.side_effect = create
    monkeypatch.setattr(utilities, 'build_vision_content', lambda *a, **k: 'synthetic image')
    settings = validate_settings({
        **{f'{stage}_effort': 'low' for stage in ('rotation', 'character_detection', 'metadata', 'crop_quality', 'locate')},
        'crop_quality_model': 'claude-sonnet-5', 'locate_model': 'claude-sonnet-5',
        'rotation_max_tokens': 32, 'crop_quality_max_tokens': 32,
        'character_detection_max_tokens': 4096, 'metadata_max_tokens': 2048,
        'locate_max_tokens': 256,
    })
    with extraction_runtime(client=client, settings=settings, usage_sink=lambda *a: None):
        get_rotation_angle(b'image', client)
        check_crop_quality(b'image', client)
        utilities.detect_book_characters([(1, 'Mia waved.')], client)
        utilities.extract_joint_metadata([(1, b'image')], client)
        utilities.locate_key_pages([(1, b'image')], client)
    for request, stage in zip(requests, ('rotation', 'crop_quality', 'character_detection', 'metadata', 'locate')):
        assert {key: request[key] for key in ('model', 'max_tokens', 'output_config')} == generation_request(settings, stage)


def test_stored_model_and_effort_validated_together(monkeypatch):
    import utilities
    overrides = {'locate_effort': 'low', 'locate_model': 'claude-sonnet-5'}
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: overrides)
    assert utilities.get_ai_settings() == validate_settings(overrides)


def test_explicit_provider_default_is_distinct_from_omission():
    settings = validate_settings({'extraction_thinking': 'adaptive', 'extraction_effort': 'high'})
    request = generation_request(settings, 'extraction')
    assert request['thinking'] == {'type': 'adaptive'}
    assert request['output_config'] == {'effort': 'high'}


def test_context_stage_forwards_generation_settings(monkeypatch):
    import extraction_core
    import utilities
    requests = []
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **request: (
        requests.append(request) or SimpleNamespace(
            content=[SimpleNamespace(type='text', text='{}')], stop_reason='end_turn'))))
    monkeypatch.setattr(utilities, 'build_vision_content', lambda *a, **k: 'synthetic image')
    settings = validate_settings({
        'prompt_variant': 'rules-plus-icl',
        'story_context_thinking': 'disabled', 'story_context_effort': 'low',
        'story_context_max_tokens': 1024,
        'story_context_limits': {'max_images': 100, 'max_request_bytes': 32000000,
                                 'max_input_tokens': 200000, 'max_output_tokens': 8192},
    })
    extraction_core.classify_book_context([{'page_number': 1, 'text': 'Mia waved.'}],
                                         [(1, b'image')], client, settings, lambda *a: None)
    assert len(requests) == 1
    assert {key: requests[0][key] for key in ('model', 'max_tokens', 'thinking', 'output_config')} == generation_request(settings, 'story_context')
