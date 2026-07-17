"""Tests for #203 — appending additional/forgotten photos to an existing book.

Covers the pure S3 helpers (``s3_constants.page_image_number`` /
``max_folder_page``) and the ``pages.uploader.append_photo_batch`` pipeline:
appended photos must become pages ``N+1 … N+k`` after the book's current
maximum, existing ``page_N`` files/docs must never be overwritten, and
``page_count`` must advance to ``N+k``.

Exercised against in-memory fakes (no network, no Streamlit runtime, no real
Firestore/S3) — same style as ``tests/test_uploader_page_isolation.py``.
"""

import io

import pytest
import streamlit as st
from PIL import Image

import pages.uploader as uploader
from s3_constants import max_folder_page, page_image_number


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------

def test_page_image_number_parses_originals_only():
    assert page_image_number("sawimages/Book/page_7.jpg") == 7
    assert page_image_number("page_12.jpg") == 12
    assert page_image_number("page_3_cropped.jpg") is None
    assert page_image_number("page_3_display.jpg") is None
    assert page_image_number("manifest.json") is None


class _ListingFs:
    def __init__(self, paths, exists=True):
        self._paths = paths
        self._exists = exists

    def exists(self, prefix):
        return self._exists

    def find(self, prefix):
        return self._paths


def test_max_folder_page_is_hole_robust():
    fs = _ListingFs([
        "sawimages/Book/page_1.jpg",
        "sawimages/Book/page_7.jpg",          # numbering hole: 2-6 missing
        "sawimages/Book/page_2_cropped.jpg",  # derived variants ignored
        "sawimages/Book/page_9_display.jpg",
    ])
    assert max_folder_page(fs, "Book") == 7


def test_max_folder_page_missing_folder_is_zero():
    assert max_folder_page(_ListingFs([], exists=False), "Nope") == 0
    assert max_folder_page(_ListingFs([]), "Empty") == 0


# ---------------------------------------------------------------------------
# In-memory fakes for the batch pipeline (mirroring test_uploader_page_isolation).
# ---------------------------------------------------------------------------

class _FakeBookRef:
    id = "test_book"


class _FakeBook:
    document_id = "test_book"
    title = "Test Book"
    photos_url = "sawimages/Test Book"

    def __init__(self, page_count):
        self.page_count = page_count
        self.photos_uploaded = True


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
    def update(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _RecordingFs:
    """In-memory S3 stand-in that records every written path and serves the
    pre-existing book pages for exists()/find()."""

    def __init__(self, existing_pages):
        self.paths = set(existing_pages)
        self.written = []

    def exists(self, path):
        if path in self.paths:
            return True
        # Directory-prefix existence (max_folder_page checks the folder).
        return any(p.startswith(path.rstrip("/") + "/") for p in self.paths)

    def find(self, prefix):
        root = prefix.rstrip("/") + "/"
        return sorted(p for p in self.paths if p.startswith(root))

    def open(self, path, mode):
        assert "w" in mode
        self.written.append(path)
        self.paths.add(path)
        return io.BytesIO()


class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


def _tiny_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def wired_session(monkeypatch):
    state = _AttrDict()
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(uploader.st, "status", lambda *a, **k: _FakeStatus())
    firestore = _FakeFirestore()
    state["firestore"] = firestore
    state["username"] = "alice"
    state["book_dict"] = {"Test Book": _FakeBookRef()}
    return state


def _existing_book_fs(n_pages):
    return _RecordingFs({
        f"sawimages/Test Book/page_{i}.jpg" for i in range(1, n_pages + 1)
    })


def _stub_ai(monkeypatch):
    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(uploader, "get_anthropic_client", lambda: object())
    monkeypatch.setattr(uploader, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))
    monkeypatch.setattr(
        uploader, "_process_page",
        lambda raw_bytes, page_number, photos_url, fs, ai_client, report=None:
        (raw_bytes, None, False),
    )
    monkeypatch.setattr(
        uploader, "extract_page_info",
        lambda bytes_for_extraction, ai_client, *, page_number, **kw:
        (f"text-{page_number}", True, "story"),
    )


# ---------------------------------------------------------------------------
# append_photo_batch.
# ---------------------------------------------------------------------------

def test_append_numbers_new_pages_after_current_max(wired_session, monkeypatch):
    _stub_ai(monkeypatch)
    book = _FakeBook(page_count=3)
    wired_session["current_book"] = book
    fs = _existing_book_fs(3)

    outcome = uploader.append_photo_batch([b"new-a", b"new-b"], fs)

    assert outcome == (3, 2, [])
    # New raw files appended as 4 and 5 — and ONLY those raw page writes.
    assert [p for p in fs.written if page_image_number(p)] == [
        "sawimages/Test Book/page_4.jpg",
        "sawimages/Test Book/page_5.jpg",
    ]
    # page_count advanced; existing pages 1-3 never rewritten.
    assert book.page_count == 5
    assert not any(f"page_{i}.jpg" in p for p in fs.written for i in (1, 2, 3))
    # New Page docs registered under the appended numbers only.
    pages_store = wired_session["firestore"].store.get("pages", {})
    assert set(pages_store) == {"test_book_4", "test_book_5"}
    assert pages_store["test_book_4"]["text"] == "text-4"
    # enter-text image cache invalidation staged.
    assert wired_session["_invalidate_image_cache"] is True


def test_append_start_is_robust_to_numbering_holes_and_stale_count(
    wired_session, monkeypatch
):
    """S3 holds a page_7 (hole / drifted page_count=3): the append must start
    beyond BOTH, so nothing existing can be overwritten."""
    _stub_ai(monkeypatch)
    book = _FakeBook(page_count=3)
    wired_session["current_book"] = book
    fs = _RecordingFs({
        "sawimages/Test Book/page_1.jpg",
        "sawimages/Test Book/page_7.jpg",
    })

    outcome = uploader.append_photo_batch([b"new-a"], fs)

    assert outcome == (7, 1, [])
    assert "sawimages/Test Book/page_8.jpg" in fs.written
    assert book.page_count == 8


def test_append_no_api_key_registers_blank_pages(wired_session, monkeypatch):
    monkeypatch.setattr(uploader, "get_anthropic_client", lambda: None)
    from utilities import AI_SETTINGS_DEFAULTS
    monkeypatch.setattr(uploader, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))
    book = _FakeBook(page_count=2)
    wired_session["current_book"] = book
    fs = _existing_book_fs(2)

    outcome = uploader.append_photo_batch([_tiny_jpeg()], fs)

    assert outcome == (2, 1, [])
    assert "sawimages/Test Book/page_3.jpg" in fs.written
    assert book.page_count == 3
    pages_store = wired_session["firestore"].store.get("pages", {})
    assert set(pages_store) == {"test_book_3"}


def test_append_collision_guard_aborts_before_writing(wired_session, monkeypatch):
    """Belt-and-braces: if page_{start+1} appears between the max computation
    and the write (another session appending), abort without touching S3."""
    _stub_ai(monkeypatch)
    book = _FakeBook(page_count=3)
    wired_session["current_book"] = book
    fs = _existing_book_fs(3)

    real_exists = fs.exists

    def racing_exists(path):
        # The max computation saw pages 1-3, but the guard's exists() check on
        # the first new filename finds a freshly-appeared page_4.
        if path.endswith("page_4.jpg"):
            return True
        return real_exists(path)

    fs.exists = racing_exists

    outcome = uploader.append_photo_batch([b"new-a"], fs)

    assert outcome is None
    assert fs.written == []
    assert book.page_count == 3
