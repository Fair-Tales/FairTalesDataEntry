"""C1: real entity writers and provider response metadata, entirely offline."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import ServiceUnavailable

import utilities
from background_pipeline import _fields_to_result, _result_to_fields
from corpus_provenance import (
    FAILURE_KINDS, apply_detection_origin, apply_page_extraction,
    page_extraction_fields,
)
from data_structures import Page, Character, Alias
from data_structures.base_structure import Field
from extraction_core import attempt_page_extraction
from pipeline_config import PIPELINE_SETTINGS_DEFAULTS
from test_enter_text_missing_page import _AttrDict, _FakeFirestore, _FakeBookRef


@pytest.fixture
def session(monkeypatch):
    state = _AttrDict(firestore=_FakeFirestore(), username='researcher')
    monkeypatch.setattr(st, 'session_state', state)
    return state


def extract(monkeypatch, data, *, stop='end_turn'):
    response = SimpleNamespace(
        model='served-model-20261116', stop_reason=stop,
        content=[SimpleNamespace(type='text', text=json.dumps(data))])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    monkeypatch.setattr(utilities, '_log_usage', lambda *args: None)
    monkeypatch.setattr(utilities, 'build_vision_content', lambda *args, **kwargs: [])
    return attempt_page_extraction(b'fixture', client,
        {**PIPELINE_SETTINGS_DEFAULTS, 'prompt_variant': 'rules-only'}, label='offline')


def assert_pipeline(origin):
    assert origin['source'] == 'ai_pipeline'
    assert origin['model'] == 'served-model-20261116'
    assert origin['requested_model'] == PIPELINE_SETTINGS_DEFAULTS['extraction_model']
    assert origin['prompt_variant'] == 'rules-only'
    assert origin['prompt_version'] != 'unknown'
    assert origin['prompt_sha256']
    assert datetime.fromisoformat(origin['timestamp']).tzinfo is not None


@pytest.mark.parametrize('reply, stop, status, failure', [
    ({'has_text': False, 'text': '', 'is_story_page': False}, 'end_turn', 'succeeded', None),
    ({}, 'end_turn', 'failed', 'invalid_response'),
    ({'text': 'partial'}, 'max_tokens', 'failed', 'truncated'),
    ({}, 'refusal', 'failed', 'refusal'),
])
def test_page_outcome_and_served_identity_survive_durable_result_and_reload(
        session, monkeypatch, reply, stop, status, failure):
    outcome, payload = extract(monkeypatch, reply, stop=stop)
    # Worker serialization must not silently strip provenance.
    fields = _result_to_fields(outcome, payload, None, False)
    outcome, payload, *_ = _fields_to_result(fields)
    page = Page(book=_FakeBookRef('book'), page_number=1)
    apply_page_extraction(page, outcome, payload)
    page.register()
    saved = Page(session.firestore.store['pages']['book_1'])
    assert saved.text == ''
    assert saved.extraction_status == status
    assert saved.extraction_failure_kind == failure
    assert_pipeline(saved.provenance_for('text'))
    assert_pipeline(saved.provenance_for('contains_story'))
    assert saved.extraction_evidence['prompt_manifest']['rendered_prompt_utf8']
    # The storage-free core must still be serializable by the offline harness.
    json.dumps(fields['extraction_evidence'])


def test_failure_kinds_match_harness():
    from scripts.extraction_eval.outcomes import FAILURE_KINDS as evaluation_kinds
    assert FAILURE_KINDS == evaluation_kinds


@pytest.mark.parametrize('entity_type', [Page, Character, Alias])
def test_old_documents_are_unknown_without_writes(session, entity_type):
    old = {'book': _FakeBookRef('book'), 'name': 'Fox', 'page_number': 1,
           'is_registered': True}
    entity = entity_type(db_object=old)
    assert entity.provenance_for('text' if entity_type is Page else 'name') == {'source': 'unknown'}
    assert entity.value_provenance == {}
    assert isinstance(entity_type.value_provenance, Field)
    if entity_type is Page:
        assert entity.extraction_status == 'unknown'
    if entity_type is Character:
        assert entity.source == 'unknown'
    if entity_type is Alias:
        assert entity.source == 'legacy_unverified'  # Preserve the established default.
    assert session.firestore.writes == []


def test_human_edit_only_reattributes_changed_value_and_is_atomic(session, monkeypatch):
    outcome, payload = extract(monkeypatch, {'has_text': True, 'text': 'A fox.', 'is_story_page': True})
    page = Page(book=_FakeBookRef('book'), page_number=1)
    apply_page_extraction(page, outcome, payload)
    page.register()
    previous_story = page.provenance_for('contains_story')
    session.firestore.writes.clear()
    page.save_edits({'text': 'The fox.'})
    saved = Page(session.firestore.store['pages']['book_1'])
    assert saved.extraction_status == 'human_entered'
    assert saved.provenance_for('text')['source'] == 'human'
    assert saved.provenance_for('text')['account'] == 'researcher'
    assert saved.provenance_for('contains_story') == previous_story
    assert len(session.firestore.writes) == 1
    patch = session.firestore.writes[0][2]
    assert patch['text'] == 'The fox.' and 'value_provenance' in patch
    page.text = page.text
    assert len(session.firestore.writes) == 1  # Rendering an unchanged field is not an edit.


def test_manual_character_and_alias_creation_and_character_edit(session):
    for kind in (Character, Alias):
        entity = kind(book=_FakeBookRef('book'))
        entity.name = 'Fox'
        entity.register()
        assert entity.source == 'human_entry'
        assert entity.provenance_for('name')['account'] == 'researcher'
    character = Character(session.firestore.store['characters']['book_fox'])
    session.username = 'validator'
    character.gender = 'Female'
    assert character.provenance_for('gender')['account'] == 'validator'
    assert character.provenance_for('name')['account'] == 'researcher'


def test_reextraction_retry_keeps_pipeline_origin_without_a_second_call(session, monkeypatch):
    page = Page(book=_FakeBookRef('book'), page_number=1)
    page.save_edits({'text': 'Manual text'})
    outcome, payload = extract(monkeypatch, {'has_text': True, 'text': 'OCR text', 'is_story_page': True})
    evidence = page_extraction_fields(outcome, payload)
    session.firestore.fail_next('update', field='contains_story')
    with pytest.raises(ServiceUnavailable):
        page.save_edits({'text': 'OCR text', 'contains_story': True}, extraction=evidence)
    assert page.provenance_for('contains_story')['source'] == 'unknown'
    page.retry_pending_edits()
    assert page.pending_extraction is None
    assert page.extraction_status == 'succeeded'
    assert_pipeline(page.provenance_for('text'))
    assert_pipeline(page.provenance_for('contains_story'))


def test_character_detection_carries_actual_response_metadata(session, monkeypatch):
    from ai_runtime import extraction_runtime
    result = {'characters': [{'name': 'Fox', 'gender': 'Male', 'human': False,
                             'protagonist': True, 'plural': False, 'aliases': ['the fox']} ]}
    response = SimpleNamespace(model='served-model-20261116', stop_reason='end_turn',
        content=[SimpleNamespace(type='text', text=json.dumps(result))])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    settings = {**PIPELINE_SETTINGS_DEFAULTS, 'prompt_variant': 'rules-only'}
    with extraction_runtime(client=client, settings=settings, usage_sink=lambda *args, **kwargs: None):
        suggestions = utilities.detect_book_characters([(1, 'Fox ran. The fox hid.')], client)
    assert_pipeline(suggestions[0]['value_origin'])
    character = Character(book=_FakeBookRef('book'))
    character.name = 'Fox'
    character.gender = 'Female'  # Reviewer changed just this field.
    apply_detection_origin(character, suggestions[0], reviewed=True, account='researcher')
    character.register()
    assert_pipeline(character.provenance_for('name'))
    assert character.provenance_for('gender')['source'] == 'human'
    assert character.provenance_for('gender')['generated_from']['model'] == 'served-model-20261116'


def test_reconstruction_labels_characters_and_aliases_as_ai(session, monkeypatch):
    import book_reconstruction as reconstruction
    from corpus_provenance import pipeline_origin
    from text_content.pipeline_prompts import prompt_request_manifest
    origin = pipeline_origin(prompt_request_manifest('character_detection', 'rules-only'),
                             requested_model=PIPELINE_SETTINGS_DEFAULTS['character_detection_model'],
                             served_model='served-model-20261116')
    suggestion = dict(name='Fox', gender='Male', human=False, protagonist=True, plural=False,
                      aliases=['the fox'], value_origin=origin)
    monkeypatch.setattr(reconstruction, 'detect_book_characters', lambda *args, **kwargs: [suggestion])
    monkeypatch.setattr(session.firestore, 'document_exists', lambda **kwargs: False, raising=False)
    book = SimpleNamespace(get_ref=lambda: _FakeBookRef('book'), add_character=lambda ref: None)
    assert reconstruction._detect_and_create_characters(book, {1: SimpleNamespace(text='A fox.', contains_story=True)},
                                             object(), None) == (1, 1)
    for collection in ('characters', 'aliases'):
        saved = next(iter(session.firestore.store[collection].values()))
        assert saved['source'] == 'ai_pipeline'
        assert_pipeline(saved['value_provenance']['name'])


@pytest.mark.parametrize('fail_commit', [False, True])
def test_admin_settings_and_audit_commit_together(session, monkeypatch, fail_commit):
    from google.cloud import firestore
    from test_batch_finalization import Database, Collection, Reference

    db = Database()
    db.store['settings/ai_pipeline'] = {'prompt_variant': 'legacy-v1', 'extraction_max_edge': 2000}
    ids = iter(range(100))
    monkeypatch.setattr(Collection, 'document',
        lambda self, name=None: Reference(self.db, f'{self.path}/{name if name is not None else next(ids)}'))

    def transactional(function):
        def wrapped(transaction):
            result = function(transaction)
            if fail_commit:
                raise ServiceUnavailable('commit refused')
            transaction.commit()
            return result
        return wrapped

    monkeypatch.setattr(firestore, 'transactional', transactional)
    wrapper = SimpleNamespace(connect_book=lambda: db,
        username_to_doc_ref=lambda name: Reference(db, 'users/' + name))
    monkeypatch.setattr(utilities, 'FirestoreWrapper', lambda **kwargs: wrapper)
    monkeypatch.setattr(utilities, 'clear_ai_settings_cache', lambda: None)
    changes = {'prompt_variant': 'rules-only', 'extraction_max_edge': 2000}
    if fail_commit:
        with pytest.raises(ServiceUnavailable):
            utilities.save_ai_settings(changes)
        assert db.store['settings/ai_pipeline']['prompt_variant'] == 'legacy-v1'
        assert not any(key.startswith('edit_log/') for key in db.store)
        return
    utilities.save_ai_settings(changes)
    assert db.store['settings/ai_pipeline'] == changes
    logs = [row for key, row in db.store.items() if key.startswith('edit_log/')]
    assert len(logs) == 1
    row = logs[0]
    assert row['field'] == 'prompt_variant'
    assert row['old_value'] == 'legacy-v1'
    assert row['new_value'] == 'rules-only'
    assert row['edited_by'].path == 'users/researcher'
    assert row['timestamp'].tzinfo is not None
    assert row['context'] == row['entity_type'] == 'pipeline_settings'
    utilities.save_ai_settings(changes)
    assert len([key for key in db.store if key.startswith('edit_log/')]) == 1


def test_imported_characters_and_aliases_label_only_supplied_values():
    from datetime import timezone
    from scripts.import_pilot_data import build_character_doc, build_alias_doc
    now = datetime.now(timezone.utc)
    character = build_character_doc(book_ref='books/fixture', name='Fox', gender='Male',
                                   protagonist=True, human=False, now=now)
    alias = build_alias_doc(book_ref='books/fixture', character_ref='characters/fox', name='the fox', now=now)
    for record in (character, alias):
        assert record['source'] == 'original_study_import'
        assert record['value_provenance']['name']['source'] == 'original_study_import'
    assert 'plural' not in character['value_provenance']  # Import placeholder has no study evidence.


def test_batch_materialization_keeps_failed_status_and_pipeline_identity(session, monkeypatch):
    import batch_finalization as finalization
    from test_batch_finalization import Database, Book
    from google.cloud import firestore
    db = Database()
    book = Book(db)
    outcome, payload = extract(monkeypatch, {}, stop='refusal')
    fields = _result_to_fields(outcome, payload, None, False)
    db.store['pipeline_jobs/job'] = {'extraction_complete': True}
    db.store['pipeline_jobs/job/pages/1'] = fields

    def transactional(function):
        def wrapped(transaction):
            result = function(transaction)
            transaction.commit()
            return result
        return wrapped

    monkeypatch.setattr(firestore, 'transactional', transactional)
    action, page = finalization._materialize_page(book, db, 'job', 1, fields, 'users/researcher')
    assert action == 'created'
    assert page.extraction_status == 'failed'
    assert page.extraction_failure_kind == 'refusal'
    assert_pipeline(page.provenance_for('text'))


def test_reviewed_detection_preserves_ai_origin_and_marks_typed_changes(session, monkeypatch):
    from test_book_characters_atomic import _CommitBook, _CommitFirestore, _load_enter_text_defs, _row
    from text_content import EnterText
    from corpus_provenance import pipeline_origin
    from text_content.pipeline_prompts import prompt_request_manifest
    origin = pipeline_origin(prompt_request_manifest('character_detection', 'rules-only'),
        requested_model=PIPELINE_SETTINGS_DEFAULTS['character_detection_model'],
        served_model='served-model-20261116')
    suggestion = dict(name='Fox', aliases=['the fox'], gender='Male', human=False,
                      protagonist=True, plural=False, value_origin=origin)
    db = _CommitFirestore()
    session.update(firestore=db, current_book=_CommitBook(), character_dict={},
                   _detected_characters=[suggestion])
    monkeypatch.setattr(st, 'rerun', lambda: None)
    row = _row('Fox', ['the fox', 'a new alias'], EnterText.review_action_create)
    row.update(gender='Female', human=False, protagonist=True)
    _load_enter_text_defs(monkeypatch)['commit_detected_characters']([row])
    saved = {data['name']: data for ref, data, _ in db.batch.sets
             if ref.path.startswith(('characters/', 'aliases/'))}
    assert_pipeline(saved['Fox']['value_provenance']['name'])
    assert saved['Fox']['value_provenance']['gender']['source'] == 'human'
    assert_pipeline(saved['the fox']['value_provenance']['name'])
    assert saved['the fox']['value_provenance']['name']['reviewed_by'] == 'researcher'
    assert saved['a new alias']['value_provenance']['name']['source'] == 'human'
    assert saved['a new alias']['value_provenance']['name']['account'] == 'researcher'
    assert saved['a new alias']['value_provenance']['name']['generated_from'] == origin
