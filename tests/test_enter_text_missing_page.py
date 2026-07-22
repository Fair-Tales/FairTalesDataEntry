"""Regression tests for #231 and #233 — enter-text must degrade gracefully when
a page's stored artefacts are missing, mirroring pages/validation.py.

#231 (silent text loss): a page whose Firestore doc is missing used to be loaded
as a bare ``Page(None)`` — no ``book``/``page_number`` and ``is_registered`` False
— so every write-through assignment (``page.text = ...``) silently no-op'd and the
typed text vanished. The fix builds the missing-doc placeholder as
``Page(page_number=n, book=<book ref>)`` (a valid ``document_id``) and, on save,
``register()``s it when unregistered — exactly as ``page_text_editor`` does.

#233 (S3-miss crash): a single missing ``sawimages/{title}/page_N*.jpg`` used to
raise ``FileNotFoundError`` from the BARE ``load_image`` call sites and lock the
archivist out of EVERY page of the book. The fix guards those sites with
``except FileNotFoundError`` and shows a per-page "image missing" notice.

The ``Page``-level behaviour underpinning #231 is exercised directly against
in-memory fakes (no network, no Streamlit runtime), the same style as
``tests/test_uploader_page_isolation.py``. ``pages/enter_text.py`` runs Streamlit
page code at import time (``check_authentication_status`` / ``page_layout`` / an
immediate ``display_image()``), so — as with the #198 guard in
``tests/test_reextract_refresh.py`` — the page-module wiring for both fixes is
locked in by scanning its source.
"""

import re
from datetime import datetime
from pathlib import Path

import streamlit as st
import pytest

from data_structures import Page


ENTER_TEXT_SOURCE = (
    Path(__file__).resolve().parent.parent / "pages" / "enter_text.py"
).read_text()


# ---------------------------------------------------------------------------
# In-memory fakes (mirrors tests/test_uploader_page_isolation.py).
# ---------------------------------------------------------------------------
class _AttrDict(dict):
    """session_state stand-in supporting both item and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class _FakeBookRef:
    """Stand-in for a book's Firestore DocumentReference — only ``.id`` is read
    by ``Page.document_id``."""

    def __init__(self, doc_id):
        self.id = doc_id


class _FakeDocRef:
    def __init__(self, collection, doc_id, store):
        self.path = f"{collection}/{doc_id}"
        self._collection = collection
        self._doc_id = doc_id
        self._store = store

    def set(self, data, merge=True):
        self._store.setdefault(self._collection, {})[self._doc_id] = dict(data)


class _FakeCollection:
    def __init__(self, name, store):
        self._name = name
        self._store = store

    def document(self, doc_id):
        return _FakeDocRef(self._name, doc_id, self._store)


class _FakeDb:
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        return _FakeCollection(name, self._store)


class _FakeFirestore:
    """Backs ``Page.register()`` (connect_book / username_to_doc_ref / save) AND
    the write-through ``Field`` path (update_field)."""

    def __init__(self):
        self.store = {}
        self.update_field_calls = []

    def connect_book(self):
        return _FakeDb(self.store)

    def username_to_doc_ref(self, username):
        return _FakeDocRef("users", username, {})

    def update_field(self, collection, document, field, value):
        self.update_field_calls.append((collection, document, field, value))
        self.store.setdefault(collection, {}).setdefault(document, {})[field] = value


@pytest.fixture
def session():
    firestore = _FakeFirestore()
    state = _AttrDict()
    state['firestore'] = firestore
    state['username'] = 'alice'
    real_state = st.session_state
    st.session_state = state
    yield state
    st.session_state = real_state


# ---------------------------------------------------------------------------
# (a) Missing snapshot -> placeholder Page with a valid document_id, unregistered.
# ---------------------------------------------------------------------------
def test_missing_page_placeholder_has_document_id_and_is_unregistered(session):
    book_ref = _FakeBookRef("the_gruffalo")

    page = Page(page_number=3, book=book_ref)

    # A valid document_id (book ref + page number) — the whole point of #231:
    # without book/page_number set (the old ``Page(None)``) this crashes.
    assert page.document_id == "the_gruffalo_3"
    assert page.is_registered is False
    assert page.text == ""


def test_old_bare_page_none_has_no_usable_document_id():
    """Locks in WHY the fix is needed: the previous ``Page(None)`` placeholder
    has no book, so ``document_id`` cannot be formed."""
    page = Page(None)
    assert page.is_registered is False
    with pytest.raises(AttributeError):
        _ = page.document_id


# ---------------------------------------------------------------------------
# (b) Saving typed text on an unregistered page registers it (creates the doc);
#     an untouched page does not.
# ---------------------------------------------------------------------------
def test_typed_text_on_unregistered_page_registers_and_persists(session):
    firestore = session['firestore']
    page = Page(page_number=3, book=_FakeBookRef("the_gruffalo"))

    # Write-through on an UNREGISTERED page is a silent no-op — nothing persisted
    # yet (this is exactly the #231 bug the register() below repairs).
    page.text = "Once upon a time."
    assert 'pages' not in firestore.store

    # The fix: register the page so the typed text is saved as an initial doc.
    page.register()

    assert page.is_registered is True
    stored = firestore.store['pages']['the_gruffalo_3']
    assert stored['text'] == "Once upon a time."
    assert stored['page_number'] == 3
    # register() stamps provenance so a created record is indistinguishable from
    # one entered the normal way.
    assert stored['entered_by'] is not None
    assert isinstance(stored['datetime_created'], datetime)


def test_untouched_missing_page_creates_no_document(session):
    firestore = session['firestore']

    # Simply constructing the placeholder (an untouched page the archivist paged
    # past) must never write a doc.
    Page(page_number=4, book=_FakeBookRef("the_gruffalo"))

    assert firestore.store == {}


def test_registered_page_write_through_updates_single_field(session):
    firestore = session['firestore']
    page = Page(page_number=3, book=_FakeBookRef("the_gruffalo"))
    page.register()
    firestore.update_field_calls.clear()

    # Once registered, further edits write through the single field (the normal
    # path a page with a real doc follows).
    page.text = "Edited text."

    assert ('pages', 'the_gruffalo_3', 'text', "Edited text.") in \
        firestore.update_field_calls


# ---------------------------------------------------------------------------
# (c) Page-module wiring for both fixes (enter_text.py can't be imported —
#     it runs Streamlit page code at import; scan its source, cf. #198).
# ---------------------------------------------------------------------------
def test_create_page_dict_builds_placeholder_for_missing_doc():
    # #231: a missing snapshot must yield Page(page_number=..., book=<ref>), not
    # the silent-loss Page(None).
    assert re.search(
        r"Page\(page_number=page_num,\s*book=book_ref\)", ENTER_TEXT_SOURCE
    ), "create_page_dict_from_db must build a placeholder Page with a valid id (#231)"
    assert "Page(snap.to_dict() if snap is not None else None)" not in ENTER_TEXT_SOURCE


def test_save_registers_unregistered_page():
    # #231: the save/persist paths must register() an unregistered page rather
    # than let the write-through Field silently no-op.
    assert ".register()" in ENTER_TEXT_SOURCE
    assert "is_registered" in ENTER_TEXT_SOURCE
    assert "EnterText.page_no_record_notice" in ENTER_TEXT_SOURCE


def test_display_paths_guard_missing_image():
    # #233: every load_image call site that could hit a missing S3 image is
    # wrapped so one missing photo cannot crash the whole page. There are three
    # (inline display, crop dialog, enlarge) plus the pre-existing prefetch guard.
    assert ENTER_TEXT_SOURCE.count("except FileNotFoundError") >= 4
    # The user-facing missing-image notice is shown at each guarded call site.
    assert ENTER_TEXT_SOURCE.count("EnterText.page_image_missing") >= 3


def test_missing_image_strings_exist():
    from text_content import EnterText

    assert isinstance(EnterText.page_image_missing, str) and EnterText.page_image_missing
    assert isinstance(EnterText.page_no_record_notice, str) and EnterText.page_no_record_notice
