"""Offline invariants for lossless aliases and compare-and-swap persistence."""
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import FailedPrecondition, AlreadyExists

from alias_schema import canonical_bundle, derive_canonical, effective_canonical, recorded_aliases
from data_structures.alias import Alias


class Ref:
    def __init__(self, path, backend=None):
        self.path, self.id = path, path.rsplit('/', 1)[-1]
        self.backend = backend

    def update(self, values, option=None):
        self.backend.attempts.append((self.path, values, option))
        if option._last_update_time != self.backend.version:
            raise FailedPrecondition('concurrent edit')
        self.backend.data.update(values)
        self.backend.version += 1
        return SimpleNamespace(update_time=self.backend.version)

    def create(self, values):
        if self.backend.data:
            raise AlreadyExists('record exists')
        self.backend.data.update(values)
        self.backend.version += 1
        return SimpleNamespace(update_time=self.backend.version)


class DB:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.version = 1
        self.attempts = []

    def collection(self, name):
        return SimpleNamespace(document=lambda doc_id: Ref(name + '/' + doc_id, self))


class State(dict):
    __getattr__ = dict.__getitem__


@pytest.fixture
def backend(monkeypatch):
    db = DB()
    wrapper = SimpleNamespace(connect_book=lambda: db, username_to_doc_ref=lambda name: Ref('users/' + name))
    monkeypatch.setattr(st, 'session_state', State(firestore=wrapper, username='student'))
    return db


def loaded(db, **changes):
    payload = {'name': 'the Little Whale', 'book': Ref('books/story'),
               'character': Ref('characters/whale'), 'storage_id': 'actual_legacy_ID',
               'is_registered': True, '_update_time': db.version, **changes}
    db.data = {key: value for key, value in payload.items() if not key.startswith('_')}
    return Alias(db_object=payload)


@pytest.mark.parametrize('raw, canonical', [
    ('  The LITTLE Whale! ', 'the little whale'),
    ("Marlon’s mum", "marlon's mum"),
    ('his mum', 'his mum'), ('a NOT-small whale', 'a not small whale'),
    ('ＦＯＸ', 'fox'), ('Straße', 'strasse'), ('???', '???'),
])
def test_canonical_preserves_articles_possessors_and_exact_raw(raw, canonical):
    alias = Alias(book=Ref('books/story'))
    alias.name = raw
    alias._prepare_registration()
    assert alias.name == raw
    assert alias.canonical_form == canonical
    assert derive_canonical(raw) == canonical


def test_override_survives_surface_edit_and_explicit_clear(backend):
    alias = loaded(backend, **canonical_bundle('the Little Whale', 'Chosen display'))
    alias.name = 'the smaller whale'
    assert alias.canonical_form == 'Chosen display'
    assert alias.canonical_override is True
    assert backend.data['raw_history'] == ['the Little Whale']
    assert alias.effective_canonical()['status'] == 'valid'
    alias.set_canonical_override('')
    assert alias.canonical_form == 'the smaller whale'
    assert alias.canonical_override is False
    assert alias.document_id == 'actual_legacy_ID'


def test_new_entry_override_survives_later_surface_edit(backend):
    alias = Alias(book=Ref('books/story'))
    alias.character = Ref('characters/whale')
    alias.name = 'whale'
    alias.set_canonical_override('Whale family')
    alias.name = 'the whale'
    alias.register()
    assert backend.data['name'] == 'the whale'
    assert backend.data['canonical_form'] == 'Whale family'
    assert backend.data['canonical_override'] is True


@pytest.mark.parametrize('projection', [
    {}, {'canonical_form': None}, {'canonical_form': float('nan')},
    {'canonical_form': []}, {'canonical_form': ''},
    {**canonical_bundle('the Little Whale'), 'canonical_rule_version': 'unknown'},
    {**canonical_bundle('old raw')},
    {**canonical_bundle('the Little Whale'), 'canonical_source': ['invalid']},
])
def test_absent_and_invalid_projection_fallback_never_writes(backend, projection):
    alias = loaded(backend, **projection)
    assert alias.effective_canonical()['canonical_form'] == 'the little whale'
    assert alias.effective_canonical()['canonical_source'] == 'legacy_fallback'
    assert backend.attempts == []
    assert alias.name == 'the Little Whale'
    assert effective_canonical(backend.data)['canonical_form'] == 'the little whale'


def test_stored_id_is_used_without_write_during_load(backend):
    snap = SimpleNamespace(id='NOT_the_name_formula', update_time=1,
                           to_dict=lambda: {'name': 'Whale', 'book': Ref('books/story')})
    alias = Alias.from_snapshot(snap)
    assert alias.document_id == 'NOT_the_name_formula'
    assert backend.attempts == []


def test_identity_and_exact_surface_are_part_of_new_storage_id():
    aliases = []
    for identity, surface in [('one', 'The fox'), ('two', 'The fox'), ('one', 'the fox')]:
        alias = Alias(book=Ref('books/story'))
        alias.character, alias.name = Ref('characters/' + identity), surface
        aliases.append(alias)
    assert len({alias.document_id for alias in aliases}) == 3
    before = aliases[0].document_id
    aliases[0].set_canonical_override('anything')
    assert aliases[0].document_id == before


def test_concurrent_override_rejects_stale_derived_write(backend):
    alias = loaded(backend)
    backend.data.update(canonical_bundle(alias.name, 'Human choice'))
    backend.version += 1
    with pytest.raises(FailedPrecondition):
        alias.update_fields_conditionally(canonical_bundle(alias.name), expected_update_time=1,
                                           firestore_client=backend)
    assert backend.data['canonical_form'] == 'Human choice'
    assert alias.canonical_form == ''


def test_conditional_update_rejects_changed_raw_hash_and_partial_bundle(backend):
    alias = loaded(backend)
    with pytest.raises(ValueError, match='Raw surface changed'):
        alias.update_fields_conditionally(canonical_bundle(alias.name), expected_update_time=1,
                                           expected_raw_hash='stale', firestore_client=backend)
    with pytest.raises(ValueError, match='atomic bundle'):
        alias.update_fields_conditionally({'canonical_form': 'partial'}, expected_update_time=1,
                                           firestore_client=backend)
    assert backend.attempts == []


def test_batch_create_acknowledged_only_after_success(backend):
    staged = []
    batch = SimpleNamespace(create=lambda ref, data: staged.append((ref, data)))
    alias = Alias(book=Ref('books/story'))
    alias.character, alias.name = Ref('characters/whale'), 'The whale'
    acknowledge = alias.register_batched(batch)
    assert len(staged) == 1
    assert staged[0][1]['canonical_form'] == 'the whale'
    assert not alias.is_registered
    acknowledge()
    assert alias.is_registered


def test_retirement_keeps_raw_surface_and_attachment(backend):
    alias = loaded(backend)
    old_ref = alias.character
    alias.delete()
    assert backend.data['name'] == 'the Little Whale'
    assert backend.data['character'] is old_ref
    assert backend.data['entry_status'] == 'rejected'


def test_structured_alias_adapter_preserves_case_articles_commas_and_duplicates():
    raw = ['the fox', 'The fox', 'fox', 'Alfie, Ava, Ben', 'the fox']
    assert recorded_aliases(raw) == raw
    assert recorded_aliases([{'surface': 'Alfie, Ava'}]) == ['Alfie, Ava']
    assert recorded_aliases(' the fox, The fox ') == ['the fox', 'The fox']



def test_structured_and_rejected_suggestions_remain_in_review_audit(monkeypatch):
    from test_book_characters_atomic import _CommitBook, _CommitFirestore, _load_enter_text_defs, _row
    from text_content import EnterText
    db = _CommitFirestore()
    book = _CommitBook()
    state = State(firestore=db, current_book=book, username='student', character_dict={},
                  _detected_characters=[{'name': 'Fox', 'aliases': ['the Fox', 'Alfie, Ava']}])
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(st, 'rerun', lambda: None)
    commit = _load_enter_text_defs(monkeypatch)['commit_detected_characters']
    commit([_row('Fox', ['the Fox', 'Alfie, Ava'], EnterText.review_action_skip)])
    audits = [data for ref, data, _ in db.batch.sets if ref.path.startswith('alias_review_audits/')]
    assert len(audits) == 1
    assert audits[0]['emitted'][0]['aliases'] == ['the Fox', 'Alfie, Ava']
    assert audits[0]['decisions'][0]['action'] == EnterText.review_action_skip
    assert db.batch.committed


def test_correction_retains_original_and_inherits_override(backend):
    alias = loaded(backend, **canonical_bundle('the Little Whale', 'Chosen display'))
    stages = []
    batch = SimpleNamespace(
        create=lambda ref, data: stages.append(('create', ref, data)),
        update=lambda ref, data, option: stages.append(('update', ref, data, option)),
        commit=lambda: None,
    )
    st.session_state.firestore.write_batch = lambda: batch
    correction = alias.create_correction('the Whale')
    assert correction.name == 'the Whale'
    assert correction.canonical_form == 'Chosen display'
    assert correction.canonical_override is True
    assert correction.supersedes == 'actual_legacy_ID'
    assert alias.name == 'the Little Whale'
    assert alias.entry_status == 'superseded'
    assert alias.superseded_by == correction.document_id
    assert [stage[0] for stage in stages] == ['create', 'update']
    assert stages[1][3]._last_update_time == 1


def test_failed_correction_does_not_retire_or_register_original(backend):
    alias = loaded(backend)
    def failed_commit():
        raise FailedPrecondition('concurrent edit')
    st.session_state.firestore.write_batch = lambda: SimpleNamespace(
        create=lambda *args: None, update=lambda *args, **kwargs: None, commit=failed_commit)
    with pytest.raises(FailedPrecondition):
        alias.create_correction('the Whale')
    assert alias.name == 'the Little Whale'
    assert alias.entry_status == 'unverified'
    assert alias.superseded_by is None


def test_form_orphan_is_not_attached_to_first_character(backend, monkeypatch):
    alias = loaded(backend)
    st.session_state['book_character_dict'] = {'Other': Ref('characters/other')}
    selectors = []
    monkeypatch.setattr(st, 'header', lambda *args: None)
    monkeypatch.setattr(st, 'warning', lambda *args: None)
    monkeypatch.setattr(st, 'selectbox', lambda *args, **kwargs: selectors.append(kwargs) or None)
    monkeypatch.setattr(st, 'text_input', lambda *args, **kwargs: kwargs['value'])
    monkeypatch.setattr(st, 'form_submit_button', lambda *args, **kwargs: False)
    alias.to_form()
    assert selectors[0]['index'] is None
    assert selectors[0]['key'].endswith(alias.document_id)
    assert backend.attempts == []
    assert alias.character.path == 'characters/whale'


def test_legacy_string_references_load_without_session_lookup(backend):
    alias = Alias(db_object={'name': 'Fox', 'book': 'books/story',
                             'character': 'characters/fox', 'is_registered': True})
    assert alias.book == 'books/story'
    assert alias.character == 'characters/fox'
    assert alias.document_id == 'story_fox'
    assert alias.to_dict(convert_ref_fields_to_ids=True)['character'] == 'fox'


def test_cleared_manual_override_remains_auditable(backend):
    alias = loaded(backend, **canonical_bundle('the Little Whale', 'Human spelling'))
    alias.set_canonical_override(None)
    assert alias.canonical_form == 'the little whale'
    assert alias.canonical_override is False
    assert alias.canonical_history[-1]['canonical_form'] == 'Human spelling'
    assert alias.canonical_history[-1]['canonical_override'] is True
    assert backend.data['canonical_history'][-1]['canonical_form'] == 'Human spelling'


def test_review_books_route_reaches_character_management(monkeypatch):
    import ast
    from pathlib import Path
    routes = []
    book = SimpleNamespace(photos_uploaded=True, editing=False)
    monkeypatch.setattr(st, 'session_state', State())
    monkeypatch.setattr(st, 'switch_page', routes.append)
    namespace = {'st': st}
    for path, function_name in [('pages/review_my_books.py', '_open_for_editing'),
                                ('pages/book_edit_home.py', 'manage_characters')]:
        tree = ast.parse(Path(path).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == function_name)
        exec(compile(ast.Module(body=[function], type_ignores=[]), path, 'exec'), namespace)
    namespace['_open_for_editing'](book)
    namespace['manage_characters']()
    assert routes == ['./pages/book_edit_home.py', './pages/enter_text.py']
    assert book.editing is True
    assert st.session_state['_open_manage_characters'] is True


def test_shared_conditional_write_cannot_bypass_raw_preservation(backend):
    alias = loaded(backend)
    with pytest.raises(ValueError, match='retained surface history'):
        alias.update_fields_conditionally({'name': 'Changed'}, expected_update_time=1,
                                           firestore_client=backend)
    with pytest.raises(ValueError, match='linked correction'):
        alias.update_fields_conditionally({'character': Ref('characters/other')},
                                           expected_update_time=1, firestore_client=backend)
    with pytest.raises(ValueError, match='linked correction'):
        alias.character = Ref('characters/other')
    assert backend.attempts == []
    assert alias.name == 'the Little Whale'
    assert alias.character.path == 'characters/whale'


def test_duplicate_proposed_names_retain_both_sources_as_unresolved(monkeypatch):
    from test_book_characters_atomic import _CommitBook, _CommitFirestore, _load_enter_text_defs, _row
    from text_content import EnterText
    db = _CommitFirestore()
    first = {'name': 'Fox', 'aliases': ['red fox'], 'id': 'first'}
    second = {'name': 'Fox', 'aliases': ['blue fox'], 'id': 'second'}
    state = State(firestore=db, current_book=_CommitBook(), username='student', character_dict={},
                  _detected_characters=[first, second])
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(st, 'rerun', lambda: None)
    _load_enter_text_defs(monkeypatch)['commit_detected_characters']([
        _row('Fox', ['red fox'], EnterText.review_action_create),
        _row('Fox', ['blue fox'], EnterText.review_action_create),
    ])
    assert all(ref.path.startswith('alias_review_audits/') for ref, _, _ in db.batch.sets)
    audit = db.batch.sets[0][1]
    assert [row['_source']['id'] for row in audit['decisions']] == ['first', 'second']
    assert audit['emitted'] == [first, second]
    assert any('Unresolved' in message for message in state['_detected_characters_result'])


def test_empty_review_completion_persists_filtered_raw_audit(monkeypatch):
    from test_book_characters_atomic import _CommitBook, _CommitFirestore, _load_enter_text_defs
    db = _CommitFirestore()
    book = _CommitBook()
    emitted = [{'name': 'Saved fox', 'aliases': ['the Fox']}]
    state = State(firestore=db, current_book=book, username='student', character_dict={},
                  _detected_characters=[], _detected_alias_audit=emitted,
                  _detected_alias_audit_book_id=book.get_ref().id,
                  _detected_raw_response={'characters': emitted})
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(st, 'rerun', lambda: None)
    namespace = _load_enter_text_defs(monkeypatch)
    namespace['_render_saved_cast'] = lambda element: None
    namespace['_skipped_existing_info'] = lambda element: None
    button = {}
    element = SimpleNamespace(info=lambda *args: None,
                              button=lambda *args, **kwargs: button.update(kwargs))
    namespace['character_review_form'](element)
    button['on_click'](*button['args'])
    audit = db.batch.sets[0][1]
    assert audit['emitted'] == emitted
    assert audit['raw_response'] == {'characters': emitted}
    assert db.batch.committed


def test_ordinary_descriptor_write_keeps_following_canonical_write_current(backend):
    alias = loaded(backend)
    alias.source = 'human_review'
    alias.set_canonical_override('Whale')
    assert backend.data['source'] == 'human_review'
    assert backend.data['canonical_form'] == 'Whale'
    assert alias._update_time == backend.version
