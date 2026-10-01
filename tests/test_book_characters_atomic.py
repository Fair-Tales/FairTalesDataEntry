"""Tests for #225 / #237-surfacing / #229 — concurrency-safe book<->character
membership and the detection-commit alias surfacing.

#225: ``Book.add_character`` / ``remove_character`` / ``get_character_dict`` and
``pages.enter_text.commit_detected_characters`` must change the book's
``characters`` array with Firestore ATOMIC transforms (``ArrayUnion`` /
``ArrayRemove``) rather than writing back a whole in-memory list — otherwise two
sessions editing the same book's cast (two tabs, or archivist + validator) each
hold a stale list and last-write-wins silently drops the other's characters.

#237 (surfacing only): aliases whose ``document_id`` (``<book_id>_<name>``, no
character component) collides are skipped; the user must be told which.

#229: Character/Alias must be built from the book's DocumentReference, not its
title string (a title routes through the write-through ref-field setter's
book_dict lookup, which stores ``None`` on a cache-stale miss and then crashes on
the next ``document_id`` access).

All exercised against in-memory fakes — no network, no Streamlit runtime, no
real Firestore.
"""

import pathlib

import pytest
import streamlit as st
from google.cloud.firestore_v1 import ArrayUnion, ArrayRemove

from data_structures.book import Book


# pages/enter_text.py is a Streamlit PAGE: importing it executes the whole page
# body (auth redirect, S3 filesystem, widget rendering) which needs a live
# session and secrets. To unit-test commit_detected_characters we exec only the
# module's *definitions* — the source prefix up to the first page-body statement
# — into a namespace, with check_authentication_status() (called at module top)
# stubbed. The real function object is returned with its globals bound to that
# namespace, so we exercise the shipping source without running the page.
_ENTER_TEXT_PAGE_MARKER = 'page_layout(current_page="./pages/enter_text.py")'


def _load_enter_text_defs(monkeypatch):
    monkeypatch.setattr("utilities.check_authentication_status", lambda: None)
    src_path = pathlib.Path(__file__).resolve().parents[1] / "pages" / "enter_text.py"
    prefix = src_path.read_text().partition(_ENTER_TEXT_PAGE_MARKER)[0]
    ns = {"__name__": "pages._enter_text_defs_under_test", "__file__": str(src_path)}
    exec(compile(prefix, str(src_path), "exec"), ns)
    return ns


class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCharRef:
    def __init__(self, doc_id):
        self.id = doc_id
        self.path = f"characters/{doc_id}"


class FakeSnap:
    def __init__(self, ref, name=None, exists=True):
        self.reference = ref
        self._name = name
        self.exists = exists

    def to_dict(self):
        return {'name': self._name}


class FakeBookRef:
    def __init__(self, doc_id):
        self.id = doc_id
        self.path = f"books/{doc_id}"


class FakeDocRef:
    def __init__(self, collection, doc_id):
        self.id = doc_id
        self.path = f"{collection}/{doc_id}"


class FakeCollection:
    def __init__(self, name):
        self.name = name

    def document(self, doc_id):
        return FakeDocRef(self.name, doc_id)


class FakeDb:
    def collection(self, name):
        return FakeCollection(name)


class FakeFirestore:
    def __init__(self, snaps=None, query=None):
        self.snaps = snaps or {}          # path -> FakeSnap
        self.query = list(query or [])    # list of FakeSnap (query_stream result)
        self.update_fields_calls = []     # (collection, document, values)
        self.update_field_calls = []      # (collection, document, field, value)

    def connect_book(self):
        return FakeDb()

    def update_fields(self, collection, document, values):
        self.update_fields_calls.append((collection, document, values))

    def update_field(self, collection, document, field, value):
        self.update_field_calls.append((collection, document, field, value))

    def get_all_by_references(self, refs):
        return [self.snaps.get(r.path, FakeSnap(r, exists=False)) for r in refs]

    def query_stream(self, collection, field, op, value):
        return iter(self.query)


@pytest.fixture
def session(monkeypatch):
    state = _AttrDict()
    monkeypatch.setattr(st, "session_state", state)
    return state


def _registered_book(state, fs, characters, title="My Book", is_registered=True):
    state["firestore"] = fs
    book = Book()
    # Seed under the load guard so the seeding assignments never write through.
    book.reading_from_db = True
    book.title = title
    book.characters = characters
    book.reading_from_db = False
    book.is_registered = is_registered
    return book


def _char_field_writes(fs):
    return [
        (c, d, v) for (c, d, f, v) in fs.update_field_calls if f == 'characters'
    ]


# ---------------------------------------------------------------------------
# add_character (#225)
# ---------------------------------------------------------------------------

def test_add_character_uses_arrayunion_and_updates_memory(session):
    fs = FakeFirestore()
    book = _registered_book(session, fs, [])
    ref = FakeCharRef("c1")

    book.add_character(ref)

    # In-memory list updated for this session.
    assert [r.path for r in book.characters] == ["characters/c1"]
    # Persisted as ONE atomic ArrayUnion, not a whole-list overwrite.
    assert len(fs.update_fields_calls) == 1
    collection, document, values = fs.update_fields_calls[0]
    assert collection == 'books'
    assert document == 'my_book'
    assert isinstance(values['characters'], ArrayUnion)
    assert values['characters'].values == [ref]
    assert 'last_updated' in values          # bumped in the same update call
    # Crucially: no full-list write of `characters` via update_field.
    assert _char_field_writes(fs) == []


def test_add_character_noop_when_already_linked(session):
    fs = FakeFirestore()
    book = _registered_book(session, fs, [FakeCharRef("c1")])

    book.add_character(FakeCharRef("c1"))

    assert fs.update_fields_calls == []
    assert [r.path for r in book.characters] == ["characters/c1"]


def test_add_character_unregistered_book_no_write(session):
    fs = FakeFirestore()
    book = _registered_book(session, fs, [], is_registered=False)

    book.add_character(FakeCharRef("c1"))

    # No Firestore write until the book is registered, but memory stays current.
    assert fs.update_fields_calls == []
    assert [r.path for r in book.characters] == ["characters/c1"]


# ---------------------------------------------------------------------------
# remove_character (#225)
# ---------------------------------------------------------------------------

def test_remove_character_uses_arrayremove(session):
    fs = FakeFirestore()
    book = _registered_book(session, fs, [FakeCharRef("c1"), FakeCharRef("c2")])

    book.remove_character(FakeCharRef("c1"))

    assert [r.path for r in book.characters] == ["characters/c2"]
    assert len(fs.update_fields_calls) == 1
    _, _, values = fs.update_fields_calls[0]
    assert isinstance(values['characters'], ArrayRemove)
    assert [r.path for r in values['characters'].values] == ["characters/c1"]
    assert _char_field_writes(fs) == []


def test_remove_character_noop_when_not_linked(session):
    fs = FakeFirestore()
    book = _registered_book(session, fs, [FakeCharRef("c1")])

    book.remove_character(FakeCharRef("cX"))

    assert fs.update_fields_calls == []
    assert [r.path for r in book.characters] == ["characters/c1"]


# ---------------------------------------------------------------------------
# get_character_dict repair/prune (#225)
# ---------------------------------------------------------------------------

def test_get_character_dict_prunes_dangling_with_arrayremove(session):
    c1, c2 = FakeCharRef("c1"), FakeCharRef("c2")
    fs = FakeFirestore(snaps={"characters/c1": FakeSnap(c1, name="Tom")})  # c2 gone
    book = _registered_book(session, fs, [c1, c2])

    result = book.get_character_dict()

    assert result == {"Tom": c1}
    # Only the dangling ref is removed, atomically — the live one is untouched.
    assert len(fs.update_fields_calls) == 1
    _, _, values = fs.update_fields_calls[0]
    assert isinstance(values['characters'], ArrayRemove)
    assert [r.path for r in values['characters'].values] == ["characters/c2"]
    assert [r.path for r in book.characters] == ["characters/c1"]
    # A pruned list must never be persisted as a whole-list overwrite.
    assert _char_field_writes(fs) == []


def test_get_character_dict_backfills_empty_list_with_arrayunion(session):
    c1 = FakeCharRef("c1")
    fs = FakeFirestore(
        snaps={"characters/c1": FakeSnap(c1, name="Tom")},
        query=[FakeSnap(c1, name="Tom")],  # discovered via the character's book field
    )
    book = _registered_book(session, fs, [])  # empty -> back-fill

    result = book.get_character_dict()

    assert result == {"Tom": c1}
    assert len(fs.update_fields_calls) == 1
    _, _, values = fs.update_fields_calls[0]
    assert isinstance(values['characters'], ArrayUnion)
    assert [r.path for r in values['characters'].values] == ["characters/c1"]
    assert [r.path for r in book.characters] == ["characters/c1"]


def test_get_character_dict_legacy_nonlist_plain_overwrite(session):
    c1 = FakeCharRef("c1")
    fs = FakeFirestore(
        snaps={"characters/c1": FakeSnap(c1, name="Tom")},
        query=[FakeSnap(c1, name="Tom")],
    )
    # Legacy schema stored a numeric character COUNT under `characters`.
    book = _registered_book(session, fs, 5)

    result = book.get_character_dict()

    assert result == {"Tom": c1}
    # A non-array field cannot take a transform, so the repair is a plain
    # whole-value overwrite via update_field, NOT ArrayUnion/ArrayRemove.
    assert fs.update_fields_calls == []
    char_writes = _char_field_writes(fs)
    assert len(char_writes) == 1
    assert char_writes[0][2] == [c1]
    assert book.characters == [c1]


def test_get_character_dict_no_write_when_unchanged(session):
    c1 = FakeCharRef("c1")
    fs = FakeFirestore(snaps={"characters/c1": FakeSnap(c1, name="Tom")})
    book = _registered_book(session, fs, [c1])

    book.get_character_dict()

    assert fs.update_fields_calls == []
    assert _char_field_writes(fs) == []


# ---------------------------------------------------------------------------
# #229 — Character/Alias built from a reference need no book_dict lookup
# ---------------------------------------------------------------------------

def test_entities_built_from_book_ref_have_working_document_id(session):
    from data_structures.character import Character
    from data_structures.alias import Alias

    # Deliberately NO 'book_dict' in session: a reference must never need it.
    ref = FakeBookRef("my_book")

    character = Character(book=ref)
    character.name = "Tom"
    assert character.document_id == "my_book_tom"

    alias = Alias(book=ref)
    alias.name = "Big Tom"
    assert alias.document_id == "my_book_big_tom"


# ---------------------------------------------------------------------------
# commit_detected_characters — alias surfacing (#237) + atomic link (#225/#229)
# ---------------------------------------------------------------------------

class _CommitBatch:
    def __init__(self):
        self.sets = []
        self.updates = []      # (ref, values)
        self.committed = False

    def set(self, ref, data, merge=True):
        self.sets.append((ref, data, merge))

    def update(self, ref, values):
        self.updates.append((ref, values))

    def commit(self):
        self.committed = True


class _CommitFirestore:
    def __init__(self, existing=None):
        self.existing = existing or {}     # collection -> set of ids
        self.batch = _CommitBatch()

    def connect_book(self):
        return FakeDb()

    def get_existing_ids(self, collection, ids):
        return {i for i in ids if i in self.existing.get(collection, set())}

    def write_batch(self):
        return self.batch

    def username_to_doc_ref(self, username):
        return FakeBookRef(username)


class _CommitBook:
    def __init__(self, title="my_book"):
        self.title = title
        self.characters = []
        self.reading_from_db = False

    def get_ref(self):
        return FakeBookRef(self.title)

    def get_character_dict(self):
        return {}


def _row(name, aliases, action):
    return {
        'name': name,
        'gender': 'unknown',
        'human': True,
        'protagonist': False,
        'plural': False,
        'aliases': aliases,
        'action': action,
    }


def test_commit_surfaces_skipped_aliases_and_uses_arrayunion(session, monkeypatch):
    from text_content import EnterText

    enter_text = _load_enter_text_defs(monkeypatch)
    commit_detected_characters = enter_text["commit_detected_characters"]
    monkeypatch.setattr(st, "rerun", lambda: None)

    fs = _CommitFirestore()
    book = _CommitBook()
    session["firestore"] = fs
    session["current_book"] = book
    session["username"] = "alice"
    session["character_dict"] = {}
    # Two different characters, each carrying the SAME alias name "Buddy": the
    # second collides on the book-scoped alias document_id and is dropped.
    session["_detected_characters"] = [{'name': 'Tom'}, {'name': 'Ivy'}]
    rows = [
        _row('Tom', 'Buddy', EnterText.review_action_create),
        _row('Ivy', 'Buddy', EnterText.review_action_create),
    ]

    commit_detected_characters(rows)

    messages = session["_detected_characters_result"]
    # The skipped-alias notice is surfaced (was silently dropped before #237).
    assert any("Buddy" in m and "already" in m for m in messages)
    assert EnterText.review_skipped_aliases.format(names="Buddy") in messages

    # Both characters created; exactly one "Buddy" alias survived.
    assert EnterText.review_created.format(characters=2, aliases=1) in messages

    # The book<->character link is staged as an atomic ArrayUnion, not a
    # whole-list overwrite (#225).
    assert len(fs.batch.updates) == 1
    _ref, values = fs.batch.updates[0]
    assert isinstance(values['characters'], ArrayUnion)
    assert {r.path for r in values['characters'].values} == {
        "characters/my_book_tom", "characters/my_book_ivy"
    }
    assert fs.batch.committed


def test_commit_surfaces_preexisting_alias_collision(session, monkeypatch):
    from text_content import EnterText

    enter_text = _load_enter_text_defs(monkeypatch)
    commit_detected_characters = enter_text["commit_detected_characters"]
    monkeypatch.setattr(st, "rerun", lambda: None)

    # "my_book_buddy" already exists in the aliases collection from a prior run.
    fs = _CommitFirestore(existing={'aliases': {"my_book_buddy"}})
    book = _CommitBook()
    session["firestore"] = fs
    session["current_book"] = book
    session["username"] = "alice"
    session["character_dict"] = {}
    session["_detected_characters"] = [{'name': 'Tom'}]
    rows = [_row('Tom', 'Buddy', EnterText.review_action_create)]

    commit_detected_characters(rows)

    messages = session["_detected_characters_result"]
    assert EnterText.review_skipped_aliases.format(names="Buddy") in messages
    # No alias created; the character still is.
    assert EnterText.review_created.format(characters=1, aliases=0) in messages
