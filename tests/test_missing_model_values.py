"""C2: invalid model codes stay missing across storage, forms and readers."""
# ruff: noqa: F811 - the session fixture is imported from test_corpus_provenance and used as a parameter
import json
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st

import utilities
from ai_runtime import extraction_runtime
from background_pipeline import _fields_to_result, _result_to_fields
from corpus_provenance import apply_detection_origin, apply_page_extraction
from data_structures import Character, Page
from pipeline_config import PIPELINE_SETTINGS_DEFAULTS
from test_corpus_provenance import extract, session  # noqa: F401 - pytest fixture
from test_enter_text_missing_page import _FakeBookRef


FIELDS = ('gender', 'human', 'protagonist', 'plural')


@pytest.mark.parametrize('field', ['has_text', 'is_story_page', 'text'])
@pytest.mark.parametrize('invalid', ['missing', None, 0, 'false', [], {}])
def test_page_invalid_required_field_is_durable_failure(session, monkeypatch, field, invalid):
    reply = {'has_text': True, 'text': 'A fox.', 'is_story_page': True}
    if invalid == 'missing':
        reply.pop(field)
    else:
        if field == 'text' and isinstance(invalid, str):
            invalid = False  # Any string is valid transcription.
        reply[field] = invalid
    outcome, payload = extract(monkeypatch, reply)
    assert outcome == 'error'
    assert payload.details['failure_kind'] == 'invalid_response'
    fields = _result_to_fields(outcome, payload, None, False)
    outcome, payload, *_ = _fields_to_result(fields)
    page = Page(book=_FakeBookRef('book'), page_number=1)
    apply_page_extraction(page, outcome, payload)
    page.register()
    saved = Page(session.firestore.store['pages']['book_1'])
    assert saved.extraction_status == 'failed'
    assert saved.extraction_failure_kind == 'invalid_response'
    assert saved.extraction_evidence['raw_page_output'] == reply


def detect(raw):
    response = SimpleNamespace(model='fixture-model', stop_reason='end_turn',
        content=[SimpleNamespace(type='text', text=json.dumps({'characters': [raw]}))])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    settings = {**PIPELINE_SETTINGS_DEFAULTS, 'prompt_variant': 'rules-only'}
    with extraction_runtime(client=client, settings=settings, usage_sink=lambda *a, **k: None):
        return utilities.detect_book_characters([(1, 'Fox ran.')], client)


@pytest.mark.parametrize('field', FIELDS)
@pytest.mark.parametrize('invalid', ['missing', None, 0, 'invalid', [], {}])
def test_character_invalid_attribute_kept_as_null_with_provenance(
        session, monkeypatch, field, invalid):
    import book_reconstruction as reconstruction

    raw = dict(name='Fox', gender='Female', human=False, protagonist=True, plural=False)
    if invalid == 'missing':
        raw.pop(field)
    else:
        raw[field] = invalid
    suggestions = detect(raw)
    assert len(suggestions) == 1
    assert suggestions[0][field] is None
    monkeypatch.setattr(reconstruction, 'detect_book_characters', lambda *a, **k: suggestions)
    monkeypatch.setattr(session.firestore, 'document_exists', lambda **k: False, raising=False)
    book = SimpleNamespace(get_ref=lambda: _FakeBookRef('book'), add_character=lambda ref: None)
    assert reconstruction._detect_and_create_characters(
        book, {1: SimpleNamespace(text='Fox ran.', contains_story=True)}, None, None) == (1, 0)
    saved = Character(session.firestore.store['characters']['book_fox'])
    assert saved.name == 'Fox'
    assert saved.__dict__[field] is None
    assert saved.provenance_for(field)['status'] == 'not_supplied'
    assert saved.provenance_for(field)['model'] == 'fixture-model'
    for other in set(FIELDS) - {field}:
        assert getattr(saved, other) == raw[other]
        assert 'status' not in saved.provenance_for(other)


def seed_widgets(monkeypatch, selections=None):
    selections = selections or {}
    indices = {}

    def select(label, *, options, index=0, key, **kwargs):
        indices[key] = index
        return selections.get(key, None if index is None else options[index])

    monkeypatch.setattr(st, 'selectbox', select)
    monkeypatch.setattr(st, 'text_input', lambda label, *, value, **kwargs: value)
    return indices


def test_unknown_form_render_keeps_nulls_and_provenance_until_person_chooses(session, monkeypatch):
    character = Character(book=_FakeBookRef('book'))
    character.name = 'Fox'
    for field in FIELDS:
        setattr(character, field, None)
    apply_detection_origin(character, {'name': 'Fox'})
    character.register()
    session.firestore.writes.clear()
    indices = seed_widgets(monkeypatch)
    character._render_fields(character.document_id)
    for field in FIELDS:
        assert indices[f'character_form_{field}_book_fox'] is None
        assert getattr(character, field) is None
        assert character.provenance_for(field)['status'] == 'not_supplied'
    # The legacy ethnicity/disability placeholders are outside C2.
    assert not any(field in patch for _, _, patch in session.firestore.writes for field in FIELDS)
    seed_widgets(monkeypatch, {'character_form_human_book_fox': False})
    character._render_fields(character.document_id)
    assert character.human is False
    assert character.provenance_for('human')['source'] == 'human'
    assert character.provenance_for('human')['generated_from']['status'] == 'not_supplied'


@pytest.mark.parametrize('values', [
    dict(gender='Female', human=True, protagonist=False, plural=False),
    dict(gender='Non-specific', human=False, protagonist=True, plural=True),
])
def test_persons_form_values_save_unchanged(session, monkeypatch, values):
    character = Character(book=_FakeBookRef('book'))
    character.name = 'Fox'
    seed_widgets(monkeypatch, {f'character_form_{field}_book_fox': value
                               for field, value in values.items()})
    character._render_fields(character.document_id)
    character.register()
    saved = session.firestore.store['characters']['book_fox']
    for field, value in values.items():
        assert saved[field] == value
        assert type(saved[field]) is type(value)
        assert saved['value_provenance'][field]['source'] == 'human'


def test_loading_existing_codes_does_not_reinterpret_or_write_them(session):
    stored = dict(book=_FakeBookRef('book'), name='Fox', is_registered=True,
                  gender='Non-specific', human=True, protagonist=False, plural=False)
    before = dict(stored)
    character = Character(db_object=stored)
    assert all(getattr(character, field) == stored[field] for field in FIELDS)
    assert character.value_provenance == {}
    assert stored == before
    assert session.firestore.writes == []


def test_reviewed_missing_codes_are_not_stamped_as_human_choices(session, monkeypatch):
    from test_book_characters_atomic import _CommitBook, _CommitFirestore, _load_enter_text_defs, _row
    from text_content import EnterText

    suggestion = detect({'name': 'Fox', 'gender': 'invalid'})[0]
    db = _CommitFirestore()
    session.update(firestore=db, current_book=_CommitBook(), character_dict={},
                   _detected_characters=[suggestion])
    monkeypatch.setattr(st, 'rerun', lambda: None)
    row = _row('Fox', [], EnterText.review_action_create)
    row.update(dict.fromkeys(FIELDS))
    _load_enter_text_defs(monkeypatch)['commit_detected_characters']([row])
    saved = next(data for ref, data, _ in db.batch.sets if ref.path.startswith('characters/'))
    for field in FIELDS:
        assert saved[field] is None
        assert saved['value_provenance'][field]['status'] == 'not_supplied'
        assert saved['value_provenance'][field]['source'] == 'ai_pipeline'


def test_detection_review_leaves_unknown_widgets_unselected(session, monkeypatch):
    from test_book_characters_atomic import _load_enter_text_defs

    session.update(_detected_characters=[detect({'name': 'Fox'})[0]],
                   current_book=SimpleNamespace(get_character_dict=lambda: {}))
    indices = seed_widgets(monkeypatch)
    for name in ('markdown', 'divider', 'success', 'write', 'button'):
        monkeypatch.setattr(st, name, lambda *a, **k: None)
    monkeypatch.setattr(st, 'form', lambda *a, **k: nullcontext())
    monkeypatch.setattr(st, 'text_area', lambda *a, **k: '')
    monkeypatch.setattr(st, 'form_submit_button', lambda *a, **k: False)
    namespace = _load_enter_text_defs(monkeypatch)
    namespace['_render_saved_cast'] = lambda *a: None
    namespace['character_review_form'](st)
    assert all(indices[key] is None for key in
               ('rev_gender_0', 'rev_human_0', 'rev_prot_0', 'rev_plural_0'))


def test_validation_editor_leaves_unknown_widgets_unselected(session, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'pages/validation.py'
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if isinstance(
        node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    namespace = {'__name__': 'pages._validation_c2_test'}
    exec(compile(tree, str(path), 'exec'), namespace)
    character = Character(book=_FakeBookRef('book'))
    character.name = 'Fox'
    for field in FIELDS:
        setattr(character, field, None)
    apply_detection_origin(character, {'name': 'Fox'})
    character.register()
    snapshot = SimpleNamespace(id='book_fox', exists=True,
        to_dict=lambda: session.firestore.store['characters']['book_fox'])
    ref = SimpleNamespace(id='book_fox', get=lambda: snapshot)
    book = SimpleNamespace(get_character_dict=lambda: {'Fox': ref})
    indices = seed_widgets(monkeypatch)
    monkeypatch.setattr(st, 'subheader', lambda *a: None)
    monkeypatch.setattr(st, 'form', lambda *a, **k: nullcontext())
    monkeypatch.setattr(st, 'form_submit_button', lambda *a, **k: False)
    namespace['_edit_aliases'] = lambda *a: None
    namespace['_add_character'] = lambda *a: None
    session.firestore.writes.clear()
    namespace['characters_editor'](book)
    assert all(indices[f'validation_char_{field}_book_fox'] is None for field in FIELDS)
    assert session.firestore.writes == []
