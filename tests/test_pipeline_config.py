"""Shared validation and request bytes, using synthetic inputs and fake services."""
import json
import threading
from types import SimpleNamespace

import pytest

import background_pipeline as production
import extraction_core as harness
import utilities
from ai_runtime import extraction_runtime
from pipeline_config import AI_SETTINGS_DEFAULTS, GENERATION_DEFAULTS, generation_request, validate_settings
from scripts.extraction_eval import cli, runner
from text_content.pipeline_prompts import (CHARACTER_RULE_BLOCKS, CHARACTER_RULE_REPLACEMENTS, PROMPT_VARIANTS,
                                          evaluation_prompt_context, prompt_for)
from test_background_pipeline import FakeClient as FakeDB, FakeFs, _make_job
from test_extraction_eval_core import FakeClient, image_bytes, isolated  # noqa: F401,F811 - pytest fixtures


def test_current_production_defaults_are_pinned(monkeypatch):
    admin_defaults = {
        'extraction_model': 'claude-sonnet-5',
        'metadata_model': 'claude-sonnet-5',
        'character_detection_model': 'claude-sonnet-5',
        'locate_model': 'claude-haiku-4-5',
        'rotation_model': 'claude-sonnet-4-6',
        'crop_quality_model': 'claude-haiku-4-5',
        'theme_model': 'claude-sonnet-4-6',
        'extraction_max_edge': 2000, 'extraction_max_tokens': 8192,
        'locate_max_edge': 784,
        'enable_rotation_correction': True, 'enable_crop_quality_gate': True,
    }
    expected = {**admin_defaults, 'prompt_variant': 'legacy-v1', 'character_prompt_variant': None,
                'page_schema_version': 'legacy-compatible-page-v1',
                'story_context_enabled': False, 'story_context_model': 'claude-sonnet-5',
                'story_context_max_tokens': 20000, 'story_context_limits': None,
                'metadata_reconciliation_enabled': False, **GENERATION_DEFAULTS}
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: {})
    assert AI_SETTINGS_DEFAULTS == admin_defaults
    assert utilities.get_ai_settings() == production._validated_settings({}) == expected
    assert validate_settings({}) == expected


@pytest.mark.parametrize('stage', ['extraction', 'character_detection'])
def test_app_default_scored_requests_match_baseline(stage, image_bytes, monkeypatch):  # noqa: F811 - pytest fixture
    baseline = validate_settings(json.loads((cli.PACKAGE / 'baseline.json').read_text())['settings'])
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: {})
    monkeypatch.setattr(utilities, '_log_usage', lambda *args: None)
    settings = utilities.get_ai_settings()
    assert settings == baseline
    assert settings['prompt_variant'] == 'legacy-v1'
    assert settings['story_context_enabled'] is False
    page = {'has_text': True, 'text': 'Mia waved.', 'is_story_page': True,
            'page_type': 'story'}
    reply = page if stage == 'extraction' else {'characters': []}
    live, evaluated = FakeClient(reply), FakeClient(reply)
    if stage == 'extraction':
        assert harness.attempt_page_extraction(
            image_bytes, live, settings, label='page_extraction')[0] == 'ok'
        with extraction_runtime(client=evaluated, settings=baseline, usage_sink=lambda *args: None):
            assert harness.attempt_page_extraction(
                image_bytes, evaluated, baseline, label='page_extraction')[0] == 'ok'
        prompt = prompt_for('page_extraction', 'legacy-v1')
    else:
        utilities.detect_book_characters([(1, page['text'])], live)
        harness.extract_characters([{'page_number': 1, 'contains_story': True, 'text': page['text']}],
                                   evaluated, baseline, lambda *args: None)
        prompt = prompt_for('character_detection', 'legacy-v1').format(
            pages_json=json.dumps([{'page': 1, 'text': page['text']}], ensure_ascii=False))
    assert live.requests == evaluated.requests
    request, = live.requests
    expected_generation = generation_request(baseline, stage)
    assert expected_generation == {
        'model': 'claude-sonnet-5',
        'max_tokens': 8192 if stage == 'extraction' else 20000,
    }
    assert all(request[key] == value for key, value in expected_generation.items())
    assert not {'thinking', 'output_config', 'temperature', 'top_p', 'top_k'} & request.keys()
    content = request['messages'][0]['content']
    assert (content[-1]['text'] if stage == 'extraction' else content) == prompt


@pytest.mark.parametrize('variant', [None, 'rules-only', 'rules-plus-icl'])
@pytest.mark.parametrize('previous', [None, {}])
def test_book_details_resolve_prompt_and_record_actual_variant(variant, previous, image_bytes, monkeypatch):  # noqa: F811 - pytest fixture
    stored = {} if variant is None else {'prompt_variant': variant}
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: stored)
    monkeypatch.setattr(utilities, '_log_usage', lambda *args: None)
    client = FakeClient({'title': {'value': 'Fixture book', 'evidence': [
        {'page_number': 1, 'quote': 'Fixture book'}]},
        'credits': [], 'publisher_statements': [], 'isbns': [], 'conflicts': []})
    metadata = utilities.extract_joint_metadata([(1, image_bytes)], client, previous=previous)
    assert metadata['metadata_extraction_status'] == 'completed'
    assert metadata['title'] == 'Fixture book'
    expected_variant = variant or 'rules-plus-icl'
    manifest = metadata['metadata_prompt_manifest']
    assert manifest['prompt_variant'] == expected_variant
    prompt = client.requests[0]['messages'][0]['content'][-1]['text']
    assert prompt.startswith(prompt_for('joint_metadata_extraction', expected_variant))
    assert manifest['rendered_prompt_utf8'] == prompt


@pytest.mark.parametrize('values, message', [
    ({'typo_setting': True}, 'Unknown pipeline setting: typo_setting'),
    ({'prompt_variant': 'typo'}, 'Invalid pipeline setting: prompt_variant'),
    ({'page_schema_version': 'typo'}, 'Invalid pipeline setting: page_schema_version'),
    ({'extraction_max_edge': True}, 'Invalid pipeline setting: extraction_max_edge'),
    ({'extraction_max_edge': '2000'}, 'Invalid pipeline setting: extraction_max_edge'),
    ({'extraction_max_edge': 2000.5}, 'Invalid pipeline setting: extraction_max_edge'),
    ({'extraction_max_tokens': 255}, 'Invalid pipeline setting: extraction_max_tokens'),
    ({'story_context_max_tokens': 20001}, 'Invalid pipeline setting: story_context_max_tokens'),
    ({'story_context_enabled': 'false'}, 'Invalid pipeline setting: story_context_enabled'),
    ({'metadata_reconciliation_enabled': 0}, 'Invalid pipeline setting: metadata_reconciliation_enabled'),
    ({'extraction_model': 'typo'}, 'Invalid pipeline setting: extraction_model'),
    ({'story_context_limits': {'max_images': True}}, 'Invalid pipeline setting: story_context_limits'),
])
@pytest.mark.parametrize('path', ['application', 'runtime', 'save', 'job', 'harness', 'cli'])
def test_invalid_settings_handling_at_each_entry(values, message, path, monkeypatch, tmp_path, caplog):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid settings reached storage or execution')

    monkeypatch.setattr(utilities, 'FirestoreWrapper', forbidden)
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: values)
    if path == 'application':
        # A rejected entry must not discard independent, valid stored overrides.
        stored = {**values, 'locate_max_edge': 1024}
        monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: stored)
        with caplog.at_level('WARNING', logger='utilities'):
            assert utilities.get_ai_settings() == validate_settings({'locate_max_edge': 1024})
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == 'WARNING'
        key, value = next(iter(values.items()))
        assert repr(key) in record.getMessage()
        assert repr(value) in record.getMessage()
        assert message in record.getMessage()
        return
    with pytest.raises(ValueError, match=message):
        if path == 'runtime':
            with extraction_runtime(client=FakeClient(), settings=values,
                                    usage_sink=forbidden):
                utilities.get_ai_settings()
        elif path == 'save':
            utilities.save_ai_settings(values)
        elif path == 'job':
            production.create_or_get_job(None, None, None, values, job_id='fixture',
                                         source_keys=['fixture.jpg'], entered_by='fixture')
        elif path == 'harness':
            runner.run_extract(None, None, None, None, None, {'settings': values}, loader=None)
        else:
            config_path = tmp_path / 'config.json'
            config_path.write_text(json.dumps({'settings': values}))
            cli._extract_loaded(SimpleNamespace(config=config_path), None)


def test_non_object_settings_refused(monkeypatch):
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: [])
    with pytest.raises(ValueError, match='Pipeline settings must be an object'):
        utilities.get_ai_settings()


def test_candidate_settings_survive_application_save_and_production_job(monkeypatch):
    candidate = json.loads((cli.PACKAGE / 'candidate_pipeline_v2.json').read_text())['settings']
    candidate['story_context_limits'] = {
        'max_images': 100, 'max_request_bytes': 32000000,
        'max_input_tokens': 200000, 'max_output_tokens': 8192,
    }
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: candidate)
    settings = utilities.get_ai_settings()
    assert all(settings[key] == value for key, value in candidate.items())
    writes = []
    monkeypatch.setattr(utilities, 'FirestoreWrapper', lambda **kwargs: SimpleNamespace(
        set_document=lambda *args, **kwargs: writes.append((args, kwargs))))
    monkeypatch.setattr(utilities, 'clear_ai_settings_cache', lambda: None)
    from data_structures.edit_log import EditLog
    monkeypatch.setattr(EditLog, 'save_pipeline_settings',
                        lambda wrapper, collection, document, values:
                        wrapper.set_document(collection, document, values, merge=True))
    utilities.save_ai_settings({'prompt_variant': 'rules-only', 'story_context_enabled': True})
    assert writes[0][0][2] == {'prompt_variant': 'rules-only', 'story_context_enabled': True}
    assert writes[0][1] == {'merge': True}
    db = FakeDB()
    production.create_or_get_job(None, db, None, settings, job_id='fixture',
                                 source_keys=['fixture.jpg'], entered_by='fixture')
    assert production._job_ref(db, 'fixture').get().to_dict()['ai_settings'] == settings


def test_evaluation_models_require_pinned_registry():
    with pytest.raises(ValueError, match='extraction_model'):
        validate_settings({'extraction_model': 'fixture-model'})
    assert validate_settings({'extraction_model': 'fixture-model'},
                             model_ids=['fixture-model'])['extraction_model'] == 'fixture-model'


@pytest.mark.parametrize('variant', PROMPT_VARIANTS)
def test_foreground_character_detection_uses_application_settings(variant, monkeypatch):
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: {'prompt_variant': variant})
    live, evaluated = FakeClient({'characters': []}), FakeClient({'characters': []})
    with extraction_runtime(client=live, settings={'prompt_variant': variant}, usage_sink=lambda *args: None):
        utilities.detect_book_characters([(1, 'Mia waved.')], live)
    harness.extract_characters([{'page_number': 1, 'contains_story': True, 'text': 'Mia waved.'}],
                               evaluated, validate_settings({'prompt_variant': variant}), lambda *args: None)
    assert (json.dumps(live.requests, ensure_ascii=False).encode('utf-8')
            == json.dumps(evaluated.requests, ensure_ascii=False).encode('utf-8'))


@pytest.mark.parametrize('variant', PROMPT_VARIANTS)
@pytest.mark.parametrize('schema', ['legacy-compatible-page-v1', 'page-regions-v1'])
@pytest.mark.parametrize('character_variant', [None, 'rules-only'])
def test_production_worker_and_harness_build_identical_request_bytes(
        variant, schema, character_variant, image_bytes, isolated, monkeypatch):  # noqa: F811 - pytest fixtures
    overrides = {**isolated, 'prompt_variant': variant, 'character_prompt_variant': character_variant, 'page_schema_version': schema,
                 'extraction_model': 'claude-sonnet-4-6', 'extraction_max_edge': 1024,
                 'extraction_max_tokens': 4096, 'character_detection_model': 'claude-opus-4-8',
                 'extraction_thinking': 'adaptive', 'extraction_effort': 'low',
                 'character_detection_thinking': 'adaptive', 'character_detection_effort': 'low',
                 'character_detection_max_tokens': 4096}
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: overrides)
    app_settings = production._validated_settings(utilities.get_ai_settings())
    eval_settings = validate_settings(overrides)
    page = {'has_text': True, 'text': 'Mia said, “Hello!”', 'is_story_page': True,
            'page_type': 'narrative', 'story_needs_context': False,
            'regions': [{'kind': 'narrative', 'text': 'Mia said, “Hello!”'}]}
    characters = {'characters': []}
    live, evaluated = FakeClient(page, characters), FakeClient(page, characters)
    db, fs = FakeDB(), FakeFs()
    _make_job(db, page_count=1)
    production._run_worker(fs, db, live, app_settings, 'job1', 'fixture', 1,
                           [image_bytes], threading.Event())
    job = production._job_ref(db, 'job1').get().to_dict()
    assert job['character_status'] == 'done'
    assert production._page_ref(db, 'job1', 1).get().to_dict()['status'] == 'done'
    # Synthetic book is not an example source: no leave-one-book-out exclusion.
    with evaluation_prompt_context('synthetic-fixture'):
        result = harness.extract_page(image_bytes, evaluated, eval_settings, lambda *args: None)
        assert result['status'] == 'completed'
        harness.extract_characters([{**result, 'page_number': 1}], evaluated,
                                    eval_settings, lambda *args: None)
    assert len(live.requests) == len(evaluated.requests) == 2
    for production_request, harness_request in zip(live.requests, evaluated.requests):
        assert production_request['thinking'] == {'type': 'adaptive'}
        assert production_request['output_config'] == {'effort': 'low'}
        assert production_request['max_tokens'] == 4096
        # Compare complete serialized payloads, including images, models and caps.
        assert (json.dumps(production_request, ensure_ascii=False).encode('utf-8')
                == json.dumps(harness_request, ensure_ascii=False).encode('utf-8'))


@pytest.mark.parametrize('character_variant', [None, *PROMPT_VARIANTS, *CHARACTER_RULE_BLOCKS, *CHARACTER_RULE_REPLACEMENTS])
def test_foreground_character_override_matches_harness(character_variant, monkeypatch):
    settings = validate_settings({'prompt_variant': 'rules-only', 'character_prompt_variant': character_variant})
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', lambda: settings)
    live, evaluated = FakeClient({'characters': []}), FakeClient({'characters': []})
    with extraction_runtime(client=live, settings=settings, usage_sink=lambda *args: None):
        utilities.detect_book_characters([(1, 'Mia waved.')], live)
    harness.extract_characters([{'page_number': 1, 'contains_story': True, 'text': 'Mia waved.'}],
                               evaluated, settings, lambda *args: None)
    assert live.requests == evaluated.requests
    assert live.requests[0]['messages'][0]['content'] == prompt_for(
        'character_detection', character_variant or 'rules-only').format(
            pages_json=json.dumps([{'page': 1, 'text': 'Mia waved.'}], ensure_ascii=False))


@pytest.mark.parametrize('value', ['', 'typo', False, 1])
def test_invalid_character_variant_is_rejected(value):
    with pytest.raises(ValueError, match='character_prompt_variant'):
        validate_settings({'character_prompt_variant': value})


@pytest.mark.parametrize('variant', [*CHARACTER_RULE_BLOCKS, *CHARACTER_RULE_REPLACEMENTS])
def test_character_only_variant_does_not_expand_page_choices(variant):
    with pytest.raises(ValueError, match='prompt_variant'):
        validate_settings({'prompt_variant': variant})
    with pytest.raises(ValueError, match='Unknown prompt variant'):
        prompt_for('page_extraction', variant)
