"""Tests for #170: auto-run character detection after OCR + on-demand re-run.

Covers two things:

1. The plain session-state helpers in ``utilities`` (``mark_character_autodetect_pending``
   / ``consume_pending_character_autodetect`` / ``stage_character_redetect``) that
   ``pages/uploader.py`` and ``pages/enter_text.py`` share to stage a (re-)run
   without duplicating bookkeeping in either page module (#129).
2. That ``pages.uploader._process_photo_batch`` sets the auto-detect flag once
   its per-page OCR loop finishes successfully (extends the fakes used by
   ``tests/test_uploader_page_isolation.py``).

Idempotency against duplicate characters is NOT re-tested here: it is already
guaranteed by ``pages/enter_text.py``'s existing ``commit_detected_characters``,
which checks ``firestore.document_exists`` for every character/alias it is
about to create, regardless of how many times detection itself has (re-)run.
This module only proves the new staging plumbing behaves as a one-shot,
session-state-only mechanism (no Streamlit widgets, no network, no writes).
"""

import io

import streamlit as st
import pytest

import pages.uploader as uploader
from utilities import (
    mark_character_autodetect_pending,
    consume_pending_character_autodetect,
    stage_character_redetect,
    consume_auto_run_detection,
    usable_precomputed_suggestions,
    CHARACTER_AUTODETECT_SOURCE_AUTO,
    CHARACTER_AUTODETECT_SOURCE_MANUAL,
)


# ---------------------------------------------------------------------------
# Pure session-state helpers.
# ---------------------------------------------------------------------------

def test_pending_autodetect_flag_is_consumed_exactly_once():
    session_state = {}

    # Nothing staged yet.
    assert consume_pending_character_autodetect(session_state) is False

    mark_character_autodetect_pending(session_state)
    assert session_state['_pending_character_autodetect'] is True

    # First consumption fires...
    assert consume_pending_character_autodetect(session_state) is True
    # ...and it is a one-shot: re-rendering the same page (e.g. paging through
    # the book afterwards) must not re-trigger it.
    assert consume_pending_character_autodetect(session_state) is False
    assert '_pending_character_autodetect' not in session_state


def test_stage_character_redetect_sets_auto_run_flags_and_source():
    session_state = {'_detected_characters': [{'name': 'stale'}]}

    stage_character_redetect(
        session_state, source=CHARACTER_AUTODETECT_SOURCE_AUTO, discard_previous=False
    )

    assert session_state['now_entering'] == 'detect'
    assert session_state['_auto_run_detection'] is True
    assert session_state['_detected_characters_source'] == CHARACTER_AUTODETECT_SOURCE_AUTO
    # discard_previous=False (the OCR-completion hook, nothing to discard on
    # the first run) must leave any pre-existing suggestions untouched.
    assert session_state['_detected_characters'] == [{'name': 'stale'}]


# ---------------------------------------------------------------------------
# usable_precomputed_suggestions: the landing gate that decides whether the
# background pipeline's precompute (#179) is safe to surface directly or the
# caller must fall back to a live run. This is the fix for auto-detection
# showing nothing on first landing while the manual re-run worked.
# ---------------------------------------------------------------------------

def test_usable_precompute_returns_suggestions_when_book_matches_and_nonempty():
    precomputed = {'book_id': 'the_gruffalo', 'suggestions': [{'name': 'Mouse'}]}
    assert usable_precomputed_suggestions(precomputed, 'the_gruffalo') == [{'name': 'Mouse'}]


def test_usable_precompute_none_when_empty_so_caller_falls_back_to_live_run():
    # The background detection had not produced anything to hand over yet (still
    # running when finalize collected it, or skipped/none-found): an empty list
    # must be treated as "nothing usable" so the caller runs detection live
    # rather than rendering an empty review — the core bug fix.
    precomputed = {'book_id': 'the_gruffalo', 'suggestions': []}
    assert usable_precomputed_suggestions(precomputed, 'the_gruffalo') is None


def test_usable_precompute_none_when_suggestions_missing_or_none():
    assert usable_precomputed_suggestions({'book_id': 'the_gruffalo'}, 'the_gruffalo') is None
    assert usable_precomputed_suggestions(
        {'book_id': 'the_gruffalo', 'suggestions': None}, 'the_gruffalo'
    ) is None


def test_usable_precompute_none_for_different_book_so_stash_never_leaks():
    precomputed = {'book_id': 'other_book', 'suggestions': [{'name': 'Mouse'}]}
    assert usable_precomputed_suggestions(precomputed, 'the_gruffalo') is None


def test_usable_precompute_none_when_absent():
    assert usable_precomputed_suggestions(None, 'the_gruffalo') is None


def test_stage_character_redetect_discards_previous_suggestions_by_default():
    session_state = {'_detected_characters': [{'name': 'stale'}]}

    # Default discard_previous=True is what the "Re-run character detection"
    # button uses (#170) so a re-run reflects the latest page text rather
    # than mixing runs.
    stage_character_redetect(session_state, source=CHARACTER_AUTODETECT_SOURCE_MANUAL)

    assert '_detected_characters' not in session_state
    assert session_state['_detected_characters_source'] == CHARACTER_AUTODETECT_SOURCE_MANUAL
    assert session_state['_auto_run_detection'] is True


# ---------------------------------------------------------------------------
# #227: consume_auto_run_detection is the one-shot gate that decides whether
# detect_entry() fires the paid whole-book AI call. detect_entry runs
# run_character_detection() IFF this returns True, so these tests pin the gate
# semantics that stop a failed run from re-billing on every rerun.
# (pages/enter_text.py runs Streamlit page code at import and cannot be imported
# here, so the gate — which is exactly what detect_entry branches on — is
# tested directly, like the other session-state helpers in this file.)
# ---------------------------------------------------------------------------

def test_consume_auto_run_detection_false_when_flag_absent():
    # (a) No staged run: the gate is False, so detect_entry does NOT call
    # run_character_detection — the render just shows the notice + Try again.
    session_state = {}
    assert consume_auto_run_detection(session_state) is False


def test_consume_auto_run_detection_true_when_staged_then_consumes():
    # (b) A staged run (stage_character_redetect set the flag): the gate is True,
    # so detect_entry DOES call run_character_detection...
    session_state = {}
    stage_character_redetect(session_state, source=CHARACTER_AUTODETECT_SOURCE_MANUAL)

    assert consume_auto_run_detection(session_state) is True
    # ...and consuming it clears the flag so it cannot fire twice.
    assert '_auto_run_detection' not in session_state


def test_failed_run_consumes_flag_so_a_plain_rerun_does_not_refire():
    # (c) The #227 regression guard. Model a failed detection run: the flag was
    # staged, detect_entry consumes it (gate True) and calls the AI, which
    # fails — leaving _detected_characters absent and now_entering == 'detect'.
    session_state = {'now_entering': 'detect'}
    stage_character_redetect(session_state, source=CHARACTER_AUTODETECT_SOURCE_MANUAL)

    # First render: gate authorises exactly one run.
    assert consume_auto_run_detection(session_state) is True
    # The run failed: no suggestions were produced and we are still on 'detect'.
    assert '_detected_characters' not in session_state
    assert session_state['now_entering'] == 'detect'

    # EVERY subsequent rerun that reaches detect_entry (fragment reruns from any
    # entry-column interaction; app reruns from Prev/Next, the show-original
    # toggle or a resize) must find the gate closed and NOT re-fire the billed
    # call. Before the fix the flag was popped-and-ignored, so the run re-fired.
    assert consume_auto_run_detection(session_state) is False
    assert consume_auto_run_detection(session_state) is False


def test_try_again_restages_a_run():
    # (d) The "Try again" button's on_click re-stages via stage_character_redetect
    # (source=MANUAL), re-opening the gate for exactly one more run.
    session_state = {'now_entering': 'detect'}
    # Start from the post-failure state: gate closed.
    assert consume_auto_run_detection(session_state) is False

    stage_character_redetect(session_state, source=CHARACTER_AUTODETECT_SOURCE_MANUAL)

    assert session_state['_auto_run_detection'] is True
    assert session_state['now_entering'] == 'detect'
    assert session_state['_detected_characters_source'] == CHARACTER_AUTODETECT_SOURCE_MANUAL
    # And the re-staged flag authorises one — and only one — fresh run.
    assert consume_auto_run_detection(session_state) is True
    assert consume_auto_run_detection(session_state) is False


# ---------------------------------------------------------------------------
# uploader._process_photo_batch flags the auto-detect hook after OCR (fakes
# mirror tests/test_uploader_page_isolation.py).
# ---------------------------------------------------------------------------

class _FakeBookRef:
    id = "the_gruffalo"


class _FakeBook:
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


class _FakeFs:
    def open(self, path, mode):
        return io.BytesIO()


class _AttrDict(dict):
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


def test_process_photo_batch_flags_autodetect_after_successful_ocr(wired_session, monkeypatch):
    from utilities import AI_SETTINGS_DEFAULTS

    monkeypatch.setattr(uploader, "get_anthropic_client", lambda: object())
    monkeypatch.setattr(uploader, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))
    monkeypatch.setattr(
        uploader, "_process_page",
        lambda raw_bytes, page_number, photos_url, fs, ai_client, report=None: (raw_bytes, None),
    )
    monkeypatch.setattr(
        uploader, "extract_page_info",
        lambda bytes_for_extraction, ai_client, *, page_number, **kwargs: (
            f"text-{page_number}", True, "story",
        ),
    )

    raw_bytes_list = [b"page-1-bytes", b"page-2-bytes"]
    fs = _FakeFs()

    uploader._process_photo_batch(raw_bytes_list, ["p1", "p2"], fs)

    assert wired_session["_upload_pipeline_done"] is True
    # The per-page OCR loop ran to completion, so the enter-text page should
    # auto-run character detection the next time it loads for this book.
    assert wired_session["_pending_character_autodetect"] is True


def test_process_photo_batch_does_not_flag_autodetect_without_api_key(wired_session, monkeypatch):
    from utilities import AI_SETTINGS_DEFAULTS

    # No Anthropic client configured: OCR is skipped wholesale, so there is no
    # story text yet for detection to run against.
    monkeypatch.setattr(uploader, "get_anthropic_client", lambda: None)
    monkeypatch.setattr(uploader, "get_ai_settings", lambda: dict(AI_SETTINGS_DEFAULTS))

    raw_bytes_list = [b"page-1-bytes"]
    fs = _FakeFs()

    uploader._process_photo_batch(raw_bytes_list, ["p1"], fs)

    assert wired_session["_upload_pipeline_done"] is True
    assert "_pending_character_autodetect" not in wired_session
