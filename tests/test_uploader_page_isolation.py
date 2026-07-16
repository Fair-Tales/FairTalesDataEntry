"""Regression test for the per-page isolation boundary in
``pages.uploader._process_photo_batch`` (harden-page-loop-error-logging).

Builds on the ``fix-cleanup-blank-second-half`` branch's fix for the specific
``None.strip()`` crash (a wordless-page reply blanking out every later page in
the book). This test locks in the broader, defense-in-depth guarantee: ANY
unguarded exception raised while processing one page — not just that one bug
— must be contained to that single page, logged to the ``extraction_errors``
Firestore collection, and must not prevent every later page from being
processed and registered with correct page numbering.

Exercised against in-memory fakes (no network, no Streamlit runtime, no real
Firestore/S3) — the same style as ``tests/test_extraction_error_log.py``.
"""

import io

import streamlit as st
import pytest

import pages.uploader as uploader


# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------

class _FakeBookRef:
    """Stand-in for the Firestore DocumentReference a Book resolves to."""

    id = "the_gruffalo"


class _FakeBook:
    """Stand-in for ``st.session_state['current_book']`` (a real ``Book`` has
    far more fields, but only these are read/written by ``_process_photo_batch``
    and ``extract_page_info``)."""

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
    """Backs BOTH ``Page.register()`` (via ``connect_book``/``username_to_doc_ref``)
    AND ``ExtractionErrorLog.record`` (via ``add_document``), so the fake
    doubles as the one 'firestore' the whole batch talks to."""

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
    """Stand-in for the ``st.status(...)`` context manager's yielded value.

    Real ``st.status`` returns ``None`` outside a live Streamlit script run
    (bare mode), so ``status.update(...)`` would crash before ever reaching
    the code under test; this fake makes the surrounding UI plumbing a no-op.
    """

    def update(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeFs:
    """Stand-in for the ``s3fs`` filesystem; every write goes to a throwaway
    in-memory buffer since this test only cares about the page-loop control
    flow, not the stored photo bytes."""

    def open(self, path, mode):
        return io.BytesIO()


class _AttrDict(dict):
    """Minimal stand-in for Streamlit's real ``session_state``, which supports
    BOTH ``st.session_state['x']`` and ``st.session_state.x`` access —
    ``_process_photo_batch`` uses the attribute form (e.g.
    ``st.session_state.current_book.photos_uploaded = True``), which a plain
    ``dict`` does not support."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def session(monkeypatch):
    """Replace ``streamlit.session_state`` with a dict-like fake, and stub the
    Streamlit UI calls ``_process_photo_batch`` makes that don't work outside
    a live script run."""
    state = _AttrDict()
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(uploader.st, "status", lambda *a, **k: _FakeStatus())
    return state


@pytest.fixture
def wired_session(session):
    firestore = _FakeFirestore()
    session["firestore"] = firestore
    session["username"] = "alice"
    session["book_dict"] = {"Test Book": _FakeBookRef()}
    session["current_book"] = _FakeBook()
    return session


# ---------------------------------------------------------------------------
# The regression test.
# ---------------------------------------------------------------------------

def test_one_bad_page_does_not_blank_the_rest_of_the_book(wired_session, monkeypatch):
    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(uploader, "get_anthropic_client", lambda: object())
    monkeypatch.setattr(uploader, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))

    processed_pages = []

    def fake_process_page(raw_bytes, page_number, photos_url, fs, ai_client, report=None):
        if page_number == 2:
            # Simulate a per-page crash unrelated to the AI call — e.g. a
            # corrupt photo that OpenCV/PIL cannot decode — which is exactly
            # the kind of failure the old loop had NO catch-all for.
            raise RuntimeError("corrupt photo: cannot decode")
        processed_pages.append(page_number)
        return raw_bytes, None, False

    monkeypatch.setattr(uploader, "_process_page", fake_process_page)

    def fake_extract_page_info(bytes_for_extraction, ai_client, *, page_number, **kwargs):
        return (f"text-{page_number}", True, "story")

    monkeypatch.setattr(uploader, "extract_page_info", fake_extract_page_info)

    raw_bytes_list = [b"page-1-bytes", b"page-2-bytes", b"page-3-bytes"]
    fs = _FakeFs()

    # Must not raise — this is the core regression assertion. Before the
    # isolation boundary, page 2's RuntimeError would propagate straight out
    # of _process_photo_batch and abort every later page.
    uploader._process_photo_batch(raw_bytes_list, ["p1", "p2", "p3"], fs)

    # Pages 1 and 3 went through the normal happy path...
    assert processed_pages == [1, 3]

    # ...and the batch continued through to the end (loop wasn't aborted).
    assert wired_session["_upload_pipeline_done"] is True

    firestore = wired_session["firestore"]
    pages_store = firestore.store.get("pages", {})

    # Every page number got a registered Page doc — numbering stays intact for
    # enter_text.py's per-page-number lookup, even though page 2 failed.
    assert set(pages_store.keys()) == {
        "the_gruffalo_1", "the_gruffalo_2", "the_gruffalo_3",
    }

    # Pages 1 and 3 kept their extracted text...
    assert pages_store["the_gruffalo_1"]["text"] == "text-1"
    assert pages_store["the_gruffalo_3"]["text"] == "text-3"
    # ...page 2 is a blank placeholder, not vanished and not poisoned with a
    # partial/garbage value.
    assert pages_store["the_gruffalo_2"]["text"] == ""

    # The failure was logged to extraction_errors (Task 2) with a useful
    # error_type/message identifying what actually happened.
    error_calls = [c for c in firestore.error_log_calls if c[0] == "extraction_errors"]
    assert len(error_calls) == 1
    _collection, data = error_calls[0]
    assert data["page_number"] == 2
    assert data["error_type"] == "RuntimeError"
    assert "corrupt photo" in data["error_message"]
    assert data["flow"] == "single"
    assert data["book_title"] == "Test Book"

    # The user is told which page(s) need manual entry — surfaced, not silent.
    assert 2 in [
        int(p) for p in
        _extract_failed_pages_from_extraction_partial_fail(wired_session)
    ]


def _extract_failed_pages_from_extraction_partial_fail(session):
    """``_process_photo_batch`` doesn't return ``failed_pages`` directly (it
    renders it via ``st.warning`` and moves on), so re-derive it the same way
    the function does: any page registered blank with no story-extraction is
    exactly the failed-page signal we asserted on above. This helper exists
    purely to keep the "page 2 was surfaced" assertion readable; the concrete
    proof is the ``pages_store``/``error_calls`` assertions above."""
    firestore = session["firestore"]
    return [
        page_id.rsplit("_", 1)[1]
        for page_id, data in firestore.store.get("pages", {}).items()
        if data["text"] == ""
    ]


def test_extraction_failure_and_isolation_failure_share_the_same_log_helper(
    wired_session, monkeypatch,
):
    """Task 2: the pre-existing Anthropic-API/parse failure path
    (``extract_page_info``) and the new per-page isolation boundary must both
    route through the shared ``log_extraction_error`` helper, landing in the
    same ``extraction_errors`` collection with the same shape."""
    import anthropic

    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(uploader, "get_anthropic_client", lambda: object())
    monkeypatch.setattr(uploader, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))
    monkeypatch.setattr(
        uploader, "_process_page",
        lambda raw_bytes, page_number, photos_url, fs, ai_client, report=None: (raw_bytes, None, False),
    )

    def fake_vision_json(*a, **k):
        raise anthropic.AnthropicError("model exploded")

    monkeypatch.setattr(uploader, "vision_json", fake_vision_json)

    fs = _FakeFs()
    uploader._process_photo_batch([b"only-page"], ["p1"], fs)

    firestore = wired_session["firestore"]
    error_calls = [c for c in firestore.error_log_calls if c[0] == "extraction_errors"]
    assert len(error_calls) == 1
    _collection, data = error_calls[0]
    assert data["error_type"] == "AnthropicError"
    assert data["page_number"] == 1
    assert data["model"] == AI_SETTINGS_DEFAULTS["extraction_model"]

    # The page is still registered blank, same as any other failed page.
    pages_store = firestore.store.get("pages", {})
    assert pages_store["the_gruffalo_1"]["text"] == ""
