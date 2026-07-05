"""Regression test for the per-page isolation boundary added to
``pages.add_books_batch._process_group_pages`` for issue #171.

The reference isolation boundary added to ``pages.uploader._process_photo_batch``
(harden-page-loop-error-logging, see ``tests/test_uploader_page_isolation.py``)
deliberately left this multi-book batch loop and the reconstruction loop out of
scope. This locks in the #171 follow-up fix: a single bad page in ONE book of a
multi-book batch must not blank the rest of that book's pages (or abort the
whole batch), must be logged to ``extraction_errors`` via the shared
``log_extraction_error`` helper, and must still be registered blank so page
numbering for later pages stays correct.

Exercised against in-memory fakes (no network, no Streamlit runtime, no real
Firestore/S3/secrets) — the same style as ``tests/test_uploader_page_isolation.py``.
"""

import io

import streamlit as st
import pytest


class _AttrDict(dict):
    """Minimal stand-in for Streamlit's real ``session_state``, which supports
    BOTH ``st.session_state['x']`` and ``st.session_state.x`` access (see
    ``tests/test_uploader_page_isolation.py`` for the same rationale)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class _FakeSecrets(dict):
    """Reports no key present for ANY lookup, so nothing at import time tries
    to build a real Anthropic/S3 client from actual credentials."""

    def __contains__(self, _key):
        return False


# ---------------------------------------------------------------------------
# Import-time workaround.
#
# Unlike ``pages/uploader.py``, ``pages/add_books_batch.py`` does not guard its
# page-level rendering code behind ``if __name__ == "__main__":`` — merely
# IMPORTING it (required to reach ``_process_group_pages``) unconditionally
# runs ``check_authentication_status()``, ``page_layout(...)``, and the render
# dispatch. Route the one-time import through the simplest branch (the 'done'
# summary, which touches no AWS/Anthropic secrets) with a throwaway
# authenticated session state and a secrets stand-in that never claims to hold
# a key. Both ``st.secrets`` and ``st.session_state`` are restored immediately
# afterwards; every actual test below installs its own session state via the
# ``session``/``wired_session`` fixtures, exactly as
# ``tests/test_uploader_page_isolation.py`` does.
_import_state = _AttrDict()
_import_state['authentication_status'] = True
_import_state['batch_step'] = 'done'
_import_state['batch_results'] = []

_real_secrets = st.secrets
_real_session_state = st.session_state
st.secrets = _FakeSecrets()
st.session_state = _import_state
try:
    import pages.add_books_batch as add_books_batch  # noqa: E402
finally:
    st.secrets = _real_secrets
    st.session_state = _real_session_state


# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------

class _FakeBookRef:
    """Stand-in for the Firestore DocumentReference a Book resolves to."""

    id = "the_gruffalo"


class _FakeBook:
    """Stand-in for the ``Book`` created by ``_make_book_from_metadata`` (only
    the fields ``_process_group_pages``/``extract_page_info``/
    ``log_extraction_error`` actually read/write)."""

    document_id = "the_gruffalo"
    title = "Test Book"


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
    group-processing pass talks to."""

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


class _FakeStatus:
    """Stand-in for the ``st.status(...)`` context manager's yielded value."""

    def update(self, **kwargs):
        pass


class _FakeFs:
    """Stand-in for the ``s3fs`` filesystem; every write goes to a throwaway
    in-memory buffer since this test only cares about the page-loop control
    flow, not the stored photo bytes."""

    def open(self, path, mode):
        return io.BytesIO()


@pytest.fixture
def session(monkeypatch):
    """Replace ``streamlit.session_state`` with a dict-like fake (both the
    ``streamlit`` module's and ``pages.add_books_batch``'s own reference to
    it, since the module imports ``streamlit as st`` too)."""
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

def test_one_bad_page_does_not_blank_the_rest_of_the_book(wired_session, monkeypatch):
    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(add_books_batch, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))

    book = _FakeBook()
    ai_client = object()
    processed_pages = []

    def fake_process_page(raw_bytes, page_number, photos_url, fs, client, report=None):
        if page_number == 2:
            # Simulate a per-page crash unrelated to the AI call — e.g. a
            # corrupt photo that OpenCV/PIL cannot decode — exactly the kind
            # of failure the old loop had NO catch-all for.
            raise RuntimeError("corrupt photo: cannot decode")
        processed_pages.append(page_number)
        return raw_bytes, None

    monkeypatch.setattr(add_books_batch, "_process_page", fake_process_page)

    def fake_extract_page_info(bytes_for_extraction, client, *, page_number, **kwargs):
        return (f"text-{page_number}", True, "story")

    monkeypatch.setattr(add_books_batch, "extract_page_info", fake_extract_page_info)

    group_pages = [
        ("p1", b"page-1-bytes"), ("p2", b"page-2-bytes"), ("p3", b"page-3-bytes"),
    ]
    fs = _FakeFs()
    status = _FakeStatus()

    # Must not raise — this is the core regression assertion. Before the
    # isolation boundary, page 2's RuntimeError would propagate straight out
    # of _process_group_pages and abort every later page (and, in the batch
    # caller, every later BOOK too).
    failed_pages = add_books_batch._process_group_pages(book, group_pages, fs, ai_client, status)

    # Pages 1 and 3 went through the normal happy path...
    assert processed_pages == [1, 3]
    # ...and page 2 was recorded as failed, for the "N pages couldn't be
    # processed" summary (#132-style reporting).
    assert failed_pages == [2]

    firestore = wired_session["firestore"]
    pages_store = firestore.store.get("pages", {})

    # Every page number got a registered Page doc — numbering stays intact,
    # even though page 2 failed.
    assert set(pages_store.keys()) == {
        "the_gruffalo_1", "the_gruffalo_2", "the_gruffalo_3",
    }

    # Pages 1 and 3 kept their extracted text...
    assert pages_store["the_gruffalo_1"]["text"] == "text-1"
    assert pages_store["the_gruffalo_3"]["text"] == "text-3"
    # ...page 2 is a blank placeholder, not vanished and not poisoned with a
    # partial/garbage value.
    assert pages_store["the_gruffalo_2"]["text"] == ""

    # The failure was logged to extraction_errors via the SHARED
    # log_extraction_error helper (#129), with a useful error_type/message.
    error_calls = [c for c in firestore.error_log_calls if c[0] == "extraction_errors"]
    assert len(error_calls) == 1
    _collection, data = error_calls[0]
    assert data["page_number"] == 2
    assert data["error_type"] == "RuntimeError"
    assert "corrupt photo" in data["error_message"]
    assert data["flow"] == "batch"
    assert data["book_title"] == "Test Book"


def test_register_failure_in_no_api_key_branch_does_not_abort_the_batch(
    wired_session, monkeypatch,
):
    """The "no API key" branch of ``_process_group_pages`` gained its own
    isolation boundary too: a single Firestore ``register()`` failure must not
    abort registering the rest of the book's pages."""
    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(add_books_batch, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))

    book = _FakeBook()
    group_pages = [("p1", b"1"), ("p2", b"2"), ("p3", b"3")]
    fs = _FakeFs()
    status = _FakeStatus()

    firestore = wired_session["firestore"]

    from data_structures.page import Page

    real_register = Page.register
    call_count = {"n": 0}

    def flaky_register(self):
        call_count["n"] += 1
        if self.page_number == 2:
            raise OSError("Firestore unavailable")
        return real_register(self)

    monkeypatch.setattr(Page, "register", flaky_register)

    failed_pages = add_books_batch._process_group_pages(book, group_pages, fs, ai_client=None, status=status)

    assert failed_pages == [2]
    pages_store = firestore.store.get("pages", {})
    # Pages 1 and 3 registered normally.
    assert set(pages_store.keys()) == {"the_gruffalo_1", "the_gruffalo_3"}

    error_calls = [c for c in firestore.error_log_calls if c[0] == "extraction_errors"]
    assert len(error_calls) == 1
    _collection, data = error_calls[0]
    assert data["page_number"] == 2
    assert data["error_type"] == "OSError"
    assert data["flow"] == "batch"
