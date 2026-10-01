"""Regression tests for the shared lookup-dict loaders (#232).

``load_publisher_dict`` / ``load_book_dict`` / ``load_character_dict`` are
``@st.cache_resource`` loaders shared by every session's init. They used to do a
bare subscript (``publisher.to_dict()['name']`` etc.) inside a dict
comprehension, so a single partial/malformed document (e.g. from an interrupted
registration) raised ``KeyError`` INSIDE the cached loader and broke session
init for every user until the doc was fixed.

They now loop and skip any document whose key field is missing or blank,
logging a warning that names the collection and doc id, while keeping every
well-formed document.

Exercised against in-memory fakes (no network, no real Firestore/secrets), the
same style as ``tests/test_user_home_search_guard.py``.
"""

import logging

import pytest

import utilities


class _FakeSnapshotDoc:
    """Stand-in for a Firestore DocumentSnapshot as yielded by
    ``get_all_documents_stream``: exposes ``.id``, ``.reference`` and
    ``.to_dict()`` (which returns ``None`` for a deleted/empty doc)."""

    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.reference = f"ref::{doc_id}"

    def to_dict(self):
        return self._data


class _FakeFirestoreWrapper:
    """Returns a fixed set of fake docs for any collection."""

    _docs = []

    def __init__(self, *args, **kwargs):
        pass

    def get_all_documents_stream(self, collection, select=None):
        return list(self._docs)


@pytest.fixture
def patched_wrapper(monkeypatch):
    """Point the loaders at a fake FirestoreWrapper and clear the resource
    cache before and after so each test sees fresh docs."""

    def _install(docs):
        _FakeFirestoreWrapper._docs = docs
        monkeypatch.setattr(utilities, "FirestoreWrapper", _FakeFirestoreWrapper)
        for loader in (
            utilities.load_publisher_dict,
            utilities.load_book_dict,
            utilities.load_character_dict,
        ):
            loader.clear()

    yield _install

    for loader in (
        utilities.load_publisher_dict,
        utilities.load_book_dict,
        utilities.load_character_dict,
    ):
        loader.clear()


def test_load_publisher_dict_skips_malformed(patched_wrapper, caplog):
    patched_wrapper([
        _FakeSnapshotDoc("good", {"name": "Puffin_Books"}),
        _FakeSnapshotDoc("deleted", None),        # to_dict() is None
        _FakeSnapshotDoc("blank", {"name": ""}),  # missing/blank name
        _FakeSnapshotDoc("nokey", {"town": "London"}),
    ])

    with caplog.at_level(logging.WARNING):
        result = utilities.load_publisher_dict()

    # Only the good doc survives; key formatting (underscores -> spaces) intact.
    assert result == {"Puffin Books": "ref::good"}
    # Every skipped doc is named in a warning.
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "publishers/deleted" in messages
    assert "publishers/blank" in messages
    assert "publishers/nokey" in messages


def test_load_book_dict_skips_malformed(patched_wrapper, caplog):
    patched_wrapper([
        _FakeSnapshotDoc("b1", {"title": "The Gruffalo"}),
        _FakeSnapshotDoc("b2", None),
        _FakeSnapshotDoc("b3", {"title": ""}),
    ])

    with caplog.at_level(logging.WARNING):
        result = utilities.load_book_dict()

    assert result == {"The Gruffalo": "ref::b1"}
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "books/b2" in messages
    assert "books/b3" in messages


def test_load_character_dict_skips_malformed(patched_wrapper, caplog):
    patched_wrapper([
        _FakeSnapshotDoc("c1", {"name": "Mouse"}),
        _FakeSnapshotDoc("c2", None),
        _FakeSnapshotDoc("c3", {"gender": "female"}),  # no name key
    ])

    with caplog.at_level(logging.WARNING):
        result = utilities.load_character_dict()

    assert result == {"Mouse": "ref::c1"}
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "characters/c2" in messages
    assert "characters/c3" in messages


def test_loaders_keep_all_wellformed_docs(patched_wrapper):
    patched_wrapper([
        _FakeSnapshotDoc("c1", {"name": "Mouse"}),
        _FakeSnapshotDoc("c2", {"name": "Fox"}),
    ])
    result = utilities.load_character_dict()
    assert result == {"Mouse": "ref::c1", "Fox": "ref::c2"}
