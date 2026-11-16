"""Delete/re-add and data-reader regressions, using synthetic records only."""
import ast
import csv
import io
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
import streamlit as st
from google.api_core.exceptions import AlreadyExists, FailedPrecondition
from google.cloud.firestore_v1 import ArrayUnion, ArrayRemove

from data_structures.alias import Alias
from data_structures.book import Book
from data_structures.character import Character
from test_character_identity import Reference as IdentityReference, Store as IdentityStore, State
from test_book_characters_atomic import _load_enter_text_defs, _row
from text_content import Admin, EnterText

ROOT = Path(__file__).resolve().parents[1]


class Reference(IdentityReference):
    def get(self):
        snapshot = super().get()
        snapshot.exists = snapshot.data is not None
        return snapshot

    def create(self, data):
        if self.path in self.store.documents:
            raise AlreadyExists('exists')
        self.store.put(self.path, data)
        return SimpleNamespace(update_time=self.store.versions[self.path])

    def update(self, updates, *, option=None):
        if option is not None:
            return super().update(updates, option=option)
        current = self.store.documents[self.path]
        for key, value in updates.items():
            if isinstance(value, ArrayUnion):
                paths = {ref.path for ref in current.get(key, [])}
                value = [*current.get(key, []), *(ref for ref in value.values if ref.path not in paths)]
            elif isinstance(value, ArrayRemove):
                paths = {ref.path for ref in value.values}
                value = [ref for ref in current.get(key, []) if ref.path not in paths]
            current[key] = deepcopy(value)
        self.store.versions[self.path] += 1


class Batch:
    def __init__(self):
        self.operations = []

    def create(self, ref, data):
        self.operations.append((ref, data, 'create'))

    def update(self, ref, data, option=None):
        self.operations.append((ref, data, option))

    def commit(self):
        # Check every precondition before applying any write, as Firestore does.
        for ref, _, option in self.operations:
            if option == 'create':
                if ref.path in ref.store.documents:
                    raise AlreadyExists('exists')
            elif option is not None and option._last_update_time != ref.store.versions[ref.path]:
                raise FailedPrecondition('concurrent edit')
        for ref, data, option in self.operations:
            if option == 'create':
                ref.create(data)
            else:
                ref.update(data, option=option)


class Store(IdentityStore):
    def collection(self, collection):
        return SimpleNamespace(document=lambda doc_id: Reference(self, f'{collection}/{doc_id}'))

    def username_to_doc_ref(self, username):
        return Reference(self, 'users/' + username)

    def document_exists(self, collection, doc_id):
        return f'{collection}/{doc_id}' in self.documents

    def get_existing_ids(self, collection, ids):
        return {doc_id for doc_id in ids if self.document_exists(collection, doc_id)}

    def get_all_by_references(self, refs):
        return [Reference(self, ref.path).get() for ref in refs]

    def get_all_documents_stream(self, collection):
        return [Reference(self, path).get() for path in self.documents if path.startswith(collection + '/')]

    def query_stream(self, *, collection, field, op, value):
        assert op == '=='
        return [ref for ref in self.get_all_documents_stream(collection)
                if getattr(ref.to_dict().get(field), 'path', None) == value.path]

    def update_fields(self, collection, document, values):
        Reference(self, f'{collection}/{document}').update(values)

    def update_field(self, collection, document, field, value):
        self.update_fields(collection, document, {field: value})

    def write_batch(self):
        return Batch()


@pytest.fixture
def context(monkeypatch):
    store = Store()
    state = State(firestore=store, username='student', character_dict={}, book_character_dict={})
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(st, 'rerun', lambda: None)
    monkeypatch.setattr('data_structures.character.load_character_dict', SimpleNamespace(clear=lambda: None))
    book = Book(db_object={'title': 'Book', 'characters': [], 'is_registered': True})
    store.put('books/book', book.to_dict())
    state['current_book'] = book
    character = Character(book=book.get_ref())
    character.name = 'Mum'
    character.gender = 'Female'
    character.identity_evidence = [{'old': 'evidence'}]
    character.member_of = ['old_group']
    character.mentions = [{'surface': 'old mention'}]
    character.register()
    book.add_character(character.get_ref())
    alias = Alias(book=book.get_ref())
    alias.character = character.get_ref()
    alias.name = 'Mother'
    alias.set_canonical_override('Old override')
    alias.effective_character = Reference(store, 'characters/old_target')
    alias.provenance = [{'old': 'provenance'}]
    alias.register()
    return store, state, book, character, alias


def form_widgets(monkeypatch, name):
    monkeypatch.setattr(st, 'header', lambda *a, **k: None)
    monkeypatch.setattr(st, 'text_input', lambda *a, **k: '' if 'override' in k['key'] else name)
    monkeypatch.setattr(st, 'selectbox', lambda *a, **k:
                        'Male' if 'character_form_gender_' in k['key'] else k['options'][k['index']])
    monkeypatch.setattr(st, 'checkbox', lambda *a, **k: k['value'])
    monkeypatch.setattr(st, 'form_submit_button', lambda *a, **k: True)
    monkeypatch.setattr(st, 'warning', lambda *a, **k: pytest.fail(str(a)))


def test_character_delete_then_form_readd_is_fresh_and_visible_once(context, monkeypatch):
    store, state, book, old, alias = context
    old_id, entity_id = old.document_id, old.entity_id
    old.delete()
    assert book.get_character_dict() == {}
    form_widgets(monkeypatch, 'Mum')
    fresh = Character(book=book.get_ref())
    fresh.to_form()
    assert fresh.is_registered
    assert (fresh.document_id, fresh.entity_id) == (old_id, entity_id)
    assert fresh.entry_status == 'unverified'
    assert fresh.gender == 'Male'
    assert fresh.identity_evidence == fresh.member_of == fresh.mentions == []
    assert list(book.get_character_dict()) == ['Mum']
    assert len(store.get_all_documents_stream('characters')) == 1
    assert state['now_entering'] == 'text'
    assert '_editing_character_id' not in state
    assert store.documents[alias.get_ref().path]['entry_status'] == 'rejected'


def test_alias_delete_then_form_readd_resets_override_and_keeps_history(context, monkeypatch):
    store, state, book, character, old = context
    old_id = old.document_id
    old.delete()
    state['book_character_dict'] = book.get_character_dict()
    form_widgets(monkeypatch, 'Mother')
    fresh = Alias(book=book.get_ref())
    fresh.to_form()
    assert fresh.document_id == old_id
    assert fresh.is_registered and fresh.entry_status == 'unverified'
    assert fresh.canonical_form == 'mother' and not fresh.canonical_override
    assert fresh.canonical_history[-1]['canonical_form'] == 'Old override'
    assert fresh.effective_character is None
    assert fresh.source == 'human_entry' and fresh.provenance == []
    assert len(store.get_all_documents_stream('aliases')) == 1
    assert fresh.character.path == character.get_ref().path
    assert state['now_entering'] == 'text'


def test_redetection_after_delete_suggests_and_commits_both_records(context, monkeypatch):
    store, state, book, character, alias = context
    other = store.put('aliases/unrelated', {'entry_status': 'rejected', 'name': 'Other'})
    superseded = store.put('aliases/superseded', {
        'name': 'old Mum', 'book': book.get_ref(), 'character': character.get_ref(),
        'entry_status': 'superseded', 'superseded_by': alias.document_id,
        'raw_history': ['original'], 'is_registered': True})
    history = deepcopy(store.documents[superseded.path])
    character.delete()
    assert store.documents[superseded.path] == history
    ns = _load_enter_text_defs(monkeypatch)
    suggestions = [{'name': 'Mum', 'aliases': ['Mother']}]
    kept, skipped = ns['_filter_existing_characters'](suggestions, book=book)
    assert kept == suggestions and skipped == []
    ns['commit_detected_characters'](
        [_row('Mum', ['Mother'], EnterText.review_action_create)], book=book, suggestions=kept)
    assert list(book.get_character_dict()) == ['Mum']
    assert len(store.get_all_documents_stream('characters')) == 1
    assert store.documents[alias.get_ref().path]['entry_status'] == 'unverified'
    assert store.documents[alias.get_ref().path]['source'] == 'reviewed_detection'
    assert store.documents[other.path]['entry_status'] == 'rejected'
    assert store.documents[superseded.path] == history
    assert not any('already' in msg.lower() for msg in state['_detected_characters_result'])


@pytest.mark.parametrize('kind', ['character', 'alias'])
@pytest.mark.parametrize('batched', [False, True])
def test_reactivation_rejects_concurrent_edit_without_acknowledgement(context, kind, batched):
    store, _, book, character, alias = context
    old = character if kind == 'character' else alias
    old.delete()
    snapshot = old.get_ref().get()
    fresh = Character(book=book.get_ref()) if kind == 'character' else Alias(book=book.get_ref())
    fresh.name = old.name
    if kind == 'alias':
        fresh.character = character.get_ref()
    store.versions[old.get_ref().path] += 1
    before = deepcopy(store.documents)
    if batched:
        batch = store.write_batch()
        fresh.reactivate(snapshot, batch=batch)
        with pytest.raises(FailedPrecondition):
            batch.commit()
    else:
        with pytest.raises(FailedPrecondition):
            fresh.reactivate(snapshot)
    assert not fresh.is_registered
    assert store.documents == before


def test_superseded_alias_cannot_be_reactivated_even_if_previously_rejected(context):
    store, _, book, character, alias = context
    store.documents[alias.get_ref().path].update(entry_status='rejected', superseded_by='correction')
    fresh = Alias(book=book.get_ref())
    fresh.name, fresh.character = alias.name, character.get_ref()
    with pytest.raises(ValueError, match='superseded'):
        fresh.reactivate(alias.get_ref().get())
    assert store.documents[alias.get_ref().path]['entry_status'] == 'rejected'


def test_active_character_still_routes_to_edit_without_overwriting_form_values(context, monkeypatch):
    store, state, book, character, _ = context
    before = deepcopy(store.documents)
    form_widgets(monkeypatch, 'Mum')
    Character(book=book.get_ref()).to_form()
    assert store.documents == before
    assert state['_editing_character_id'] == character.document_id
    assert state['now_entering'] == 'manage'


def test_reconstruction_detection_also_reuses_rejected_records(context, monkeypatch):
    import book_reconstruction

    store, _, book, character, alias = context
    character.delete()
    monkeypatch.setattr(book_reconstruction, 'detect_book_characters',
                        lambda *a, **k: [{'name': 'Mum', 'aliases': ['Mother']}])
    result = book_reconstruction._detect_and_create_characters(
        book, {1: SimpleNamespace(contains_story=True, text='Mum arrived.')}, None, None)
    assert result == (1, 1)
    assert list(book.get_character_dict()) == ['Mum']
    assert len(store.get_all_documents_stream('characters')) == 1
    assert len(store.get_all_documents_stream('aliases')) == 1
    assert store.documents[alias.get_ref().path]['entry_status'] == 'unverified'


def test_dashboard_counts_active_characters_only_and_never_aliases(context, monkeypatch):
    store, state, book, character, alias = context
    store.put('characters/rejected', {'name': 'Gone', 'gender': 'Female', 'entry_status': 'rejected'})
    store.put('characters/superseded', {'gender': 'Female', 'entry_status': 'superseded'})
    store.put('characters/legacy', {'name': 'Legacy', 'gender': 'Male', 'human': False})
    store.put('aliases/rejected', {'name': 'Gone alias', 'gender': 'Female', 'entry_status': 'rejected'})
    for name in ('title', 'write', 'button'):
        monkeypatch.setattr(st, name, lambda *a, **k: None)
    monkeypatch.setattr('utilities.check_authentication_status', lambda: None)
    monkeypatch.setattr('utilities.page_layout', lambda **k: None)
    source = (ROOT / 'pages/results_dashboard.py').read_text().partition('\nif scope_ids:')[0]
    ns = {}
    exec(compile(source, 'pages/results_dashboard.py', 'exec'), ns)
    assert ns['total_in_scope'] == 2
    assert ns['human_counts']['Female'] == 1
    assert ns['nonhuman_counts']['Male'] == 1
    character.delete()
    exec(compile(source, 'pages/results_dashboard.py', 'exec'), ns)
    assert ns['total_in_scope'] == 1


def test_research_csv_excludes_retired_characters_and_aliases(context, monkeypatch):
    store, _, _, _, _ = context
    for collection in ('characters', 'aliases'):
        for status in ('rejected', 'superseded', 'withdrawn'):
            store.put(f'{collection}/{status}', {'name': status, 'entry_status': status})
    monkeypatch.setattr(st, 'button', lambda *a, **k: True)
    download = {}
    monkeypatch.setattr(st, 'download_button', lambda **k: download.update(k))
    module = ast.parse((ROOT / 'pages/admin.py').read_text())
    node = next(node for node in module.body if isinstance(node, ast.If)
                and 'admin_prepare_book_download_button' in ast.unparse(node.test))
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'pages/admin.py', 'exec'),
         {'st': st, 'Admin': Admin, 'io': io, 'zipfile': zipfile, 'csv': csv})
    with zipfile.ZipFile(io.BytesIO(download['data'])) as archive:
        for collection, expected in [('characters', 'Mum'), ('aliases', 'Mother')]:
            rows = list(csv.DictReader(io.StringIO(archive.read(collection + '.csv').decode())))
            assert [row['name'] for row in rows] == [expected]
