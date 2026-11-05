"""Character identity survives edits and retirement; no student rows are deleted."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import FailedPrecondition

import data_structures.character as character_module
from data_structures.character import Character


class State(dict):
    def __getattr__(self, name):
        if name not in self:
            raise AttributeError(name)
        return self.get(name)


class Snapshot:
    def __init__(self, reference, data, version):
        self.reference = reference
        self.id = reference.id
        self.update_time = version
        self.data = deepcopy(data)

    def to_dict(self):
        return deepcopy(self.data)


class Reference:
    def __init__(self, store, path):
        self.store, self.path = store, path
        self.id = path.rsplit('/', 1)[-1]

    def __deepcopy__(self, memo):
        return self

    def get(self):
        self.store.reads.append(self.path)
        return Snapshot(self, self.store.documents.get(self.path), self.store.versions.get(self.path, 0))

    def update(self, updates, *, option):
        if self.store.before_update is not None:
            hook, self.store.before_update = self.store.before_update, None
            hook(self, updates)
        if option._last_update_time != self.store.versions.get(self.path, 0):
            raise FailedPrecondition("Concurrent human edit")
        self.store.documents[self.path].update(deepcopy(updates))
        self.store.versions[self.path] += 1
        self.store.writes.append((self.path, deepcopy(updates)))
        return SimpleNamespace(update_time=self.store.versions[self.path])

    def delete(self):
        pytest.fail("Retirement must not delete any stored document")

    def set(self, *args, **kwargs):
        pytest.fail("Renames must not replace stored documents")


class Store:
    def __init__(self):
        self.documents, self.versions = {}, {}
        self.reads, self.writes = [], []
        self.before_update = None

    def collection(self, collection):
        return SimpleNamespace(document=lambda document_id: Reference(self, f"{collection}/{document_id}"))

    def put(self, path, payload):
        self.documents[path] = deepcopy(payload)
        self.versions[path] = self.versions.get(path, 0) + 1
        return Reference(self, path)

    def connect_book(self):
        return self

    def query_stream(self, *, collection, field, op, value):
        assert (collection, field, op) == ('aliases', 'character', '==')
        return iter([
            Reference(self, path).get() for path, data in self.documents.items()
            if path.startswith('aliases/') and getattr(data.get(field), 'path', None) == value.path
        ])


@pytest.fixture
def identity_context(monkeypatch):
    store = Store()
    book_ref = Reference(store, 'books/book')
    ref = store.put('characters/actual_student_id', {
        'name': 'Mum', 'book': book_ref, 'is_registered': True,
        'student_detail': {'keep': 'original metadata'},
    })
    alias_ref = store.put('aliases/old_student_alias', {
        'name': 'her mother', 'book': book_ref, 'character': ref,
        'is_registered': True, 'student_detail': 'keep this too',
    })
    removed = []
    state = State(
        firestore=store,
        book_character_dict={'Mum': ref},
        character_dict={'Mum': ref},
        current_book=SimpleNamespace(remove_character=lambda item: removed.append(item.path)),
    )
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(character_module, 'load_character_dict', SimpleNamespace(clear=lambda: None))
    return store, state, ref, alias_ref, removed


def test_snapshot_seeds_stable_identity_without_writes_or_reference_reads(identity_context):
    store, _, ref, _, _ = identity_context
    snapshot = ref.get()
    reads = list(store.reads)
    character = Character.from_snapshot(snapshot)
    assert store.reads == reads
    assert store.writes == []
    assert character.document_id == 'actual_student_id'
    assert character.storage_id == 'actual_student_id'
    assert character.entity_id == 'characters/actual_student_id'
    assert 'storage_id' not in store.documents[ref.path]  # No migration on read.


def test_rename_keeps_reference_and_every_prior_name_and_alias(identity_context):
    store, state, ref, alias_ref, _ = identity_context
    character = Character.from_snapshot(ref.get())
    old_alias = deepcopy(store.documents[alias_ref.path])
    old_paths = set(store.documents)
    character.rename('Mother')
    character.name = '  MOTHER  '
    assert set(store.documents) == old_paths
    assert character.document_id == 'actual_student_id'
    assert character.entity_id == ref.path
    assert character.name_history == ['Mum', 'Mother']
    assert store.documents[ref.path]['name_history'] == ['Mum', 'Mother']
    assert store.documents[ref.path]['name'] == '  MOTHER  '
    assert store.documents[ref.path]['student_detail'] == {'keep': 'original metadata'}
    assert store.documents[alias_ref.path] == old_alias
    assert state['book_character_dict']['  MOTHER  '].path == ref.path
    assert 'Mum' not in state['book_character_dict']
    assert all(path == ref.path for path, _ in store.writes)


def test_noop_rename_performs_no_read_or_write(identity_context):
    store, _, ref, _, _ = identity_context
    character = Character.from_snapshot(ref.get())
    reads = list(store.reads)
    character.rename('Mum')
    assert store.reads == reads
    assert store.writes == []


def test_concurrent_rename_does_not_overwrite_human_input(identity_context):
    store, _, ref, _, _ = identity_context
    character = Character.from_snapshot(ref.get())

    def concurrent_edit(reference, updates):
        store.documents[reference.path]['name'] = 'Another archivist choice'
        store.versions[reference.path] += 1

    store.before_update = concurrent_edit
    with pytest.raises(FailedPrecondition):
        character.rename('Mother')
    assert character.name == 'Mum'
    assert character.name_history == []
    assert store.documents[ref.path]['name'] == 'Another archivist choice'
    assert store.writes == []


def test_existing_stale_name_rejected_before_write(identity_context):
    store, _, ref, _, _ = identity_context
    character = Character.from_snapshot(ref.get())
    store.documents[ref.path]['name'] = 'Other choice'
    with pytest.raises(FailedPrecondition):
        character.rename('Mother')
    assert store.writes == []


@pytest.mark.parametrize('field', ['storage_id', 'entity_id'])
def test_identity_cannot_change_through_descriptor_or_shared_writer(identity_context, field):
    store, _, ref, _, _ = identity_context
    character = Character.from_snapshot(ref.get())
    with pytest.raises(ValueError, match='immutable'):
        setattr(character, field, 'different_identity')
    with pytest.raises(ValueError, match='immutable'):
        character.update_fields_conditionally({field: 'different_identity'},
                                              expected_update_time=1, firestore_client=store)
    assert store.writes == []


def test_retirement_preserves_character_and_all_alias_raw_records(identity_context):
    store, state, ref, alias_ref, removed = identity_context
    before = deepcopy(store.documents)
    character = Character.from_snapshot(ref.get())
    character.delete()
    assert set(store.documents) == set(before)
    for path, payload in before.items():
        assert {key: value for key, value in store.documents[path].items() if key != 'entry_status'} == payload
        assert store.documents[path]['entry_status'] == 'rejected'
    assert removed == [ref.path]
    assert state['book_character_dict'] == {}
    assert state['character_dict'] == {}
    assert store.documents[alias_ref.path]['character'].path == ref.path


def test_retiring_one_book_does_not_remove_same_named_global_character(identity_context):
    store, state, ref, _, _ = identity_context
    other = Reference(store, 'characters/other_book_mum')
    state['character_dict']['Mum'] = other
    Character.from_snapshot(ref.get()).delete()
    assert state['character_dict']['Mum'] is other


def test_legacy_string_reference_and_orphan_are_readable_without_services():
    character = Character(db_object={'name': 'Mum', 'book': 'books/legacy_book', 'is_registered': True})
    assert character.document_id == 'legacy_book_mum'
    assert character.book == 'books/legacy_book'
    orphan = Character(db_object={'name': 'Mum', 'book': None, 'is_registered': True})
    assert orphan.name == 'Mum'
    assert orphan.document_id == '_mum'


def test_evidence_and_unresolved_fields_survive_load_export(identity_context):
    store, _, ref, _, _ = identity_context
    evidence = {
        'name_is_label': True,
        'mentions': [{'surface': 'her mother', 'page': 1}],
        'editorial_labels': ['Mother figure'],
        'raw_alias_entries': [{'surface': 'her mother', 'accepted': False}],
        'detection_audit': [{'status': 'unresolved_attachment'}],
        'unresolved_mentions': [{'surface': 'mum', 'candidate_entity_ids': ['one', 'two']}],
    }
    store.documents[ref.path].update(evidence)
    character = Character.from_snapshot(ref.get())
    assert {key: character.to_dict().get(key) for key in evidence} == evidence
    assert store.writes == []
