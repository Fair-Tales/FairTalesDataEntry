"""Tests for the FirestoreWrapper batched-read helpers (#78).

The N+1 hot paths (per-page loop at book open, per-character ``ref.get()`` per
render, per-character alias queries) were replaced with single batched reads.
These tests pin the helpers' contracts against a fake backend: one ``get_all``
round trip, missing-document semantics identical to the serial reads, input
order preserved, and ``in``-query chunking under Firestore's 30-value cap.
No network, no Streamlit runtime.
"""

import pytest

from utilities import FirestoreWrapper


# ---------------------------------------------------------------------------
# Fake Firestore backend.
# ---------------------------------------------------------------------------

class FakeRef:
    def __init__(self, collection, doc_id):
        self.id = doc_id
        self.path = f"{collection}/{doc_id}"


class FakeSnap:
    def __init__(self, ref, data):
        self.id = ref.id
        self.reference = ref
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return self._data


class FakeQuery:
    def __init__(self, db, collection, filters=()):
        self._db = db
        self._collection = collection
        self._filters = filters

    def where(self, filter):
        if filter.op_string == "in":
            self._db.in_chunks.append(list(filter.value))
        return FakeQuery(self._db, self._collection, self._filters + (filter,))

    def document(self, doc_id):
        return FakeRef(self._collection, doc_id)

    def stream(self):
        rows = self._db.store.get(self._collection, {})
        for doc_id, data in rows.items():
            keep = True
            for f in self._filters:
                # google FieldFilter exposes field_path/op_string/value.
                value = data.get(f.field_path)
                if f.op_string == "in":
                    keep = keep and value in f.value
                elif f.op_string == "==":
                    keep = keep and value == f.value
                else:  # pragma: no cover - unused op in these tests
                    raise AssertionError(f"unexpected op {f.op_string}")
            if keep:
                yield FakeSnap(FakeRef(self._collection, doc_id), data)


class FakeDb:
    def __init__(self, store):
        self.store = store           # {collection: {doc_id: data}}
        self.get_all_calls = 0
        self.in_chunks = []          # every 'in' filter's value list, in order

    def collection(self, name):
        return FakeQuery(self, name)

    def get_all(self, refs):
        self.get_all_calls += 1
        # Arbitrary (reversed) order, as the real get_all does not preserve
        # request order — the helpers must not rely on it.
        for ref in reversed(list(refs)):
            collection = ref.path.split("/")[0]
            yield FakeSnap(ref, self.store.get(collection, {}).get(ref.id))


@pytest.fixture
def wrapper():
    """A FirestoreWrapper bound to the fake backend (bypasses st.secrets)."""
    fake_db = FakeDb({
        'pages': {
            'book_1': {'page_number': 1, 'text': 'one'},
            'book_2': {'page_number': 2, 'text': 'two'},
            'book_4': {'page_number': 4, 'text': 'four'},
        },
        'characters': {
            'c1': {'name': 'Tom'},
            'c2': {'name': 'Ivy'},
        },
        'aliases': {
            'a1': {'name': 'Tommy', 'character': 'characters/c1'},
            'a2': {'name': 'Ivy-Lou', 'character': 'characters/c2'},
            'a3': {'name': 'Big Tom', 'character': 'characters/c1'},
        },
    })
    w = FirestoreWrapper.__new__(FirestoreWrapper)
    w.auth = False
    w.connect_book = lambda auth=None: fake_db
    return w, fake_db


# ---------------------------------------------------------------------------
# get_all_by_ids — the book-open page loop replacement.
# ---------------------------------------------------------------------------

def test_get_all_by_ids_single_round_trip(wrapper):
    w, db = wrapper
    snaps = w.get_all_by_ids('pages', ['book_1', 'book_2', 'book_4'])
    assert db.get_all_calls == 1
    assert snaps['book_2'].to_dict() == {'page_number': 2, 'text': 'two'}


def test_get_all_by_ids_missing_doc_matches_get_by_reference_semantics(wrapper):
    # A missing page doc must read back as exists=False / to_dict()=None —
    # exactly what the old serial get_by_reference loop produced.
    w, _db = wrapper
    snaps = w.get_all_by_ids('pages', ['book_1', 'book_3'])
    assert set(snaps) == {'book_1', 'book_3'}
    assert snaps['book_3'].exists is False
    assert snaps['book_3'].to_dict() is None


def test_get_all_by_ids_empty_input_no_round_trip(wrapper):
    w, db = wrapper
    assert w.get_all_by_ids('pages', []) == {}
    assert db.get_all_calls == 0


# ---------------------------------------------------------------------------
# get_all_by_references — the per-character ref.get() replacement.
# ---------------------------------------------------------------------------

def test_get_all_by_references_preserves_input_order(wrapper):
    # The fake's get_all yields in REVERSED order; the helper must reorder to
    # the input, since Book.get_character_dict's insertion order depends on it.
    w, db = wrapper
    refs = [FakeRef('characters', 'c1'), FakeRef('characters', 'c2')]
    snaps = w.get_all_by_references(refs)
    assert db.get_all_calls == 1
    assert [s.to_dict()['name'] for s in snaps] == ['Tom', 'Ivy']


def test_get_all_by_references_missing_doc_and_empty(wrapper):
    w, db = wrapper
    refs = [FakeRef('characters', 'c1'), FakeRef('characters', 'gone')]
    snaps = w.get_all_by_references(refs)
    assert snaps[0].exists is True
    assert snaps[1].exists is False
    assert w.get_all_by_references([]) == []
    assert db.get_all_calls == 1


# ---------------------------------------------------------------------------
# query_stream_in — the per-character alias-query replacement.
# ---------------------------------------------------------------------------

def test_query_stream_in_matches_per_value_equality_queries(wrapper):
    w, _db = wrapper
    got = {s.to_dict()['name'] for s in w.query_stream_in(
        'aliases', 'character', ['characters/c1', 'characters/c2'],
    )}
    assert got == {'Tommy', 'Ivy-Lou', 'Big Tom'}


def test_query_stream_in_chunks_under_firestore_cap(wrapper):
    w, db = wrapper
    values = [f"characters/x{i}" for i in range(65)]
    list(w.query_stream_in('aliases', 'character', values))
    # 65 values -> chunks of 30, 30, 5; never above the server's 30-value cap.
    assert [len(c) for c in db.in_chunks] == [30, 30, 5]
    assert [v for chunk in db.in_chunks for v in chunk] == values


def test_query_stream_in_empty_values_yields_nothing(wrapper):
    w, db = wrapper
    assert list(w.query_stream_in('aliases', 'character', [])) == []
    assert db.in_chunks == []
