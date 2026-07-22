"""Regression test for the per-page isolation boundary added to
``book_reconstruction._process_pages`` for issue #171.

The reference isolation boundary added to ``pages.uploader._process_photo_batch``
(harden-page-loop-error-logging, see ``tests/test_uploader_page_isolation.py``)
deliberately left the reconstruction loop (and the batch-multi-book loop) out of
scope. This locks in the #171 follow-up fix: a single bad page must not abort
the whole reconstruction (including the character/alias detection pass and the
book's completion that run afterwards), must be logged to ``extraction_errors``
via the shared ``log_extraction_error`` helper, and must still be registered
blank so page numbering for later pages stays correct.

Exercised against in-memory fakes (no network, no Streamlit runtime, no real
Firestore/S3) — the same style as ``tests/test_uploader_page_isolation.py``.
"""

import io

import streamlit as st
import pytest

import book_reconstruction


# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------

class _FakeBookRef:
    """Stand-in for the Firestore DocumentReference a Book resolves to."""

    id = "the_gruffalo"


class _FakeBook:
    """Stand-in for the ``Book`` being reconstructed (only the fields
    ``_process_pages``/``extract_page_info``/``log_extraction_error`` actually
    read/write)."""

    document_id = "the_gruffalo"
    title = "Test Book"

    def get_ref(self):
        return _FakeBookRef()


class _FakeDocRef:
    def __init__(self, path):
        self.path = path


class _FakeFirestoreDoc:
    def __init__(self, collection, doc_id, store):
        self._collection = collection
        self._doc_id = doc_id
        self._store = store

    def set(self, data, merge=True):
        self._store.setdefault(self._collection, {})[self._doc_id] = data


class _FakeFirestoreCollection:
    def __init__(self, name, store):
        self._name = name
        self._store = store

    def document(self, doc_id):
        return _FakeFirestoreDoc(self._name, doc_id, self._store)


class _FakeDb:
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        return _FakeFirestoreCollection(name, self._store)


class _FakeFirestore:
    """Backs BOTH ``Page.register()`` (via ``connect_book``/
    ``username_to_doc_ref``) AND ``ExtractionErrorLog.record`` (via
    ``add_document``), so the fake doubles as the one 'firestore' the whole
    reconstruction pass talks to."""

    def __init__(self):
        self.store = {}
        self.error_log_calls = []

    def connect_book(self):
        return _FakeDb(self.store)

    def username_to_doc_ref(self, username):
        return _FakeDocRef(f"users/{username}")

    def add_document(self, collection, data):
        self.error_log_calls.append((collection, data))
        return _FakeDocRef(f"{collection}/auto-id-{len(self.error_log_calls)}")


class _FakeFs:
    """Stand-in for the ``s3fs`` filesystem; every write goes to a throwaway
    in-memory buffer since this test only cares about the page-loop control
    flow, not the stored photo bytes."""

    def open(self, path, mode):
        return io.BytesIO()


class _AttrDict(dict):
    """Minimal stand-in for Streamlit's real ``session_state`` (see
    ``tests/test_uploader_page_isolation.py`` for the same rationale)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def session(monkeypatch):
    state = _AttrDict()
    monkeypatch.setattr(st, "session_state", state)
    return state


@pytest.fixture
def wired_session(session):
    firestore = _FakeFirestore()
    session["firestore"] = firestore
    session["username"] = "alice"
    session["book_dict"] = {"Test Book": _FakeBookRef()}
    return session


# ---------------------------------------------------------------------------
# The regression test.
# ---------------------------------------------------------------------------

def test_one_bad_page_does_not_abort_the_reconstruction(wired_session, monkeypatch):
    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(
        book_reconstruction, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS)
    )

    book = _FakeBook()
    client = object()
    processed_pages = []

    def fake_process_page(raw_bytes, page_number, photos_url, fs, ai_client, report=None):
        if page_number == 2:
            # Simulate a per-page crash unrelated to the AI call — e.g. a
            # corrupt photo that OpenCV/PIL cannot decode — exactly the kind
            # of failure the old loop had NO catch-all for.
            raise RuntimeError("corrupt photo: cannot decode")
        processed_pages.append(page_number)
        # Mirror the real _process_page 3-tuple (bytes, method, rotation_uncertain)
        # — a drifted 2-tuple mock hid a production unpack crash (#217 arity).
        return raw_bytes, None, False

    monkeypatch.setattr(book_reconstruction, "_process_page", fake_process_page)

    def fake_extract_page_info(bytes_for_extraction, ai_client, *, page_number, **kwargs):
        return (f"text-{page_number}", True, "story")

    monkeypatch.setattr(book_reconstruction, "extract_page_info", fake_extract_page_info)

    pages = [("p1", b"page-1-bytes"), ("p2", b"page-2-bytes"), ("p3", b"page-3-bytes")]
    fs = _FakeFs()

    # Must not raise — this is the core regression assertion. Before the
    # isolation boundary, page 2's RuntimeError would propagate straight out
    # of _process_pages and abort the WHOLE reconstruction (character/alias
    # detection and book completion never run).
    page_objs, failed_pages = book_reconstruction._process_pages(book, pages, client, fs, progress=None)

    # Pages 1 and 3 went through the normal happy path...
    assert processed_pages == [1, 3]
    assert failed_pages == [2]

    # Every page number got a Page object back, in order, so later stages
    # (character detection, page_count) see correct numbering.
    assert set(page_objs.keys()) == {1, 2, 3}

    firestore = wired_session["firestore"]
    pages_store = firestore.store.get("pages", {})
    assert set(pages_store.keys()) == {
        "the_gruffalo_1", "the_gruffalo_2", "the_gruffalo_3",
    }

    # Pages 1 and 3 kept their extracted text...
    assert pages_store["the_gruffalo_1"]["text"] == "text-1"
    assert pages_store["the_gruffalo_3"]["text"] == "text-3"
    # ...page 2 is a blank placeholder, not vanished and not poisoned.
    assert pages_store["the_gruffalo_2"]["text"] == ""
    # ...and its in-memory Page object reflects the same safe defaults, so the
    # subsequent character-detection pass skips it cleanly.
    assert page_objs[2].contains_story is False
    assert page_objs[2].text == ""

    # The failure was logged to extraction_errors via the SHARED
    # log_extraction_error helper (#129), with a useful error_type/message.
    error_calls = [c for c in firestore.error_log_calls if c[0] == "extraction_errors"]
    assert len(error_calls) == 1
    _collection, data = error_calls[0]
    assert data["page_number"] == 2
    assert data["error_type"] == "RuntimeError"
    assert "corrupt photo" in data["error_message"]
    assert data["flow"] == "reconstruction"
    assert data["book_title"] == "Test Book"


def test_extraction_failure_and_isolation_failure_share_the_same_log_helper(
    wired_session, monkeypatch,
):
    """The pre-existing Anthropic-API/parse failure path (``extract_page_info``,
    caught via the narrow ``except PageExtractionError``) and the new per-page
    isolation boundary must both route through the shared
    ``log_extraction_error`` helper, landing in the same ``extraction_errors``
    collection with the same shape (mirrors the uploader test of the same
    name)."""
    from utilities import AI_SETTINGS_DEFAULTS
    from pages.uploader import PageExtractionError

    monkeypatch.setattr(
        book_reconstruction, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS)
    )
    monkeypatch.setattr(
        book_reconstruction, "_process_page",
        lambda raw_bytes, page_number, photos_url, fs, ai_client, report=None: (raw_bytes, None, False),
    )

    def fake_extract_page_info(*a, **k):
        raise PageExtractionError("AnthropicError", "model exploded")

    monkeypatch.setattr(book_reconstruction, "extract_page_info", fake_extract_page_info)

    book = _FakeBook()
    fs = _FakeFs()
    page_objs, failed_pages = book_reconstruction._process_pages(
        book, [("p1", b"only-page")], object(), fs, progress=None
    )

    assert failed_pages == [1]
    firestore = wired_session["firestore"]
    pages_store = firestore.store.get("pages", {})
    assert pages_store["the_gruffalo_1"]["text"] == ""
    # This failure was already logged by extract_page_info itself (mocked away
    # here), so the isolation boundary's own log_extraction_error call must NOT
    # fire a second time for the same page.
    error_calls = [c for c in firestore.error_log_calls if c[0] == "extraction_errors"]
    assert error_calls == []
