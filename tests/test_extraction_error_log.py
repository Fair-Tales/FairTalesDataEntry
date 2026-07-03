"""Unit tests for the AI page-extraction error handling (issue #132).

Two units are exercised, both against in-memory fakes (no network, no Streamlit
runtime):

  * ``ExtractionErrorLog.record`` — writes a correctly-shaped document to the
    ``extraction_errors`` collection, coerces references to path strings, and is
    fully guarded so a logging failure never propagates.
  * ``pages.uploader.extract_page_info`` — on a genuine Anthropic API error or an
    unparseable reply it writes to the error log and raises ``PageExtractionError``
    (the failure signal callers count), and on success returns the plain tuple.

The live Firestore write is Chris's to smoke-test where the secrets exist; this
file locks in the pure decision + schema logic.
"""

import streamlit as st
import anthropic
import pytest

import data_structures.extraction_error_log as eel
from data_structures import ExtractionErrorLog
import pages.uploader as uploader
from pages.uploader import extract_page_info, PageExtractionError


# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------

class _FakeRef:
    """Stand-in for a Firestore DocumentReference (only ``path`` is read)."""

    def __init__(self, path):
        self.path = path


class _FakeFirestore:
    """Captures ``add_document`` calls; mimics the real wrapper's return value."""

    def __init__(self, raises=False):
        self.calls = []
        self.raises = raises

    def add_document(self, collection, data):
        if self.raises:
            raise RuntimeError("firestore unavailable")
        self.calls.append((collection, data))
        return _FakeRef(f"{collection}/auto-id")


@pytest.fixture
def session(monkeypatch):
    """Replace ``streamlit.session_state`` with a plain dict for the test."""
    state = {}
    monkeypatch.setattr(st, "session_state", state)
    return state


# ---------------------------------------------------------------------------
# ExtractionErrorLog.record
# ---------------------------------------------------------------------------

def test_record_writes_expected_schema(session):
    firestore = _FakeFirestore()
    session["firestore"] = firestore

    ref = ExtractionErrorLog.record(
        book_id="the_gruffalo",
        book_title="The Gruffalo",
        page_number=3,
        page_name="page_3.jpg",
        error_type="InternalServerError",
        error_message="the model exploded",
        username="alice",
        flow=ExtractionErrorLog.FLOW_SINGLE,
    )

    assert ref is not None
    assert len(firestore.calls) == 1
    collection, data = firestore.calls[0]
    assert collection == "extraction_errors"
    assert data["book_id"] == "the_gruffalo"
    assert data["book_title"] == "The Gruffalo"
    assert data["page_number"] == 3
    assert data["page_name"] == "page_3.jpg"
    assert data["error_type"] == "InternalServerError"
    assert data["error_message"] == "the model exploded"
    assert data["username"] == "alice"
    assert data["flow"] == "single"
    # timestamp is UTC-aware.
    assert data["timestamp"].tzinfo is not None


def test_record_coerces_reference_values_to_paths(session):
    firestore = _FakeFirestore()
    session["firestore"] = firestore

    ExtractionErrorLog.record(
        book_id=_FakeRef("books/the_gruffalo"),
        book_title="The Gruffalo",
        page_number=1,
        error_type="parse_error",
        error_message="bad json",
        username=_FakeRef("users/alice"),
        flow=ExtractionErrorLog.FLOW_BATCH,
    )

    _collection, data = firestore.calls[0]
    assert data["book_id"] == "books/the_gruffalo"
    assert data["username"] == "users/alice"


def test_record_is_guarded_against_write_failure(session):
    """A firestore that raises must not propagate — the upload must not break."""
    session["firestore"] = _FakeFirestore(raises=True)

    ref = ExtractionErrorLog.record(
        book_id="b", book_title="B", page_number=1,
        error_type="x", error_message="y",
    )
    assert ref is None


def test_record_returns_none_when_no_firestore(session):
    # session has no 'firestore' key.
    ref = ExtractionErrorLog.record(
        book_id="b", book_title="B", page_number=1,
        error_type="x", error_message="y",
    )
    assert ref is None


# ---------------------------------------------------------------------------
# extract_page_info failure contract
# ---------------------------------------------------------------------------

class _FakeBook:
    document_id = "the_gruffalo"
    title = "The Gruffalo"


@pytest.fixture
def captured_log(monkeypatch):
    """Capture ExtractionErrorLog.record calls made from within uploader."""
    calls = []

    def _fake_record(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(uploader.ExtractionErrorLog, "record", _fake_record)
    return calls


def test_extract_page_info_success_returns_tuple(session, captured_log, monkeypatch):
    monkeypatch.setattr(
        uploader, "vision_json",
        lambda *a, **k: ({"text": "  hi  ", "is_story_page": True,
                          "page_type": "story"}, "raw"),
    )
    result = extract_page_info(b"img", client=object(), book=_FakeBook(),
                               page_number=2, flow="single")
    assert result == ("hi", True, "story")
    assert captured_log == []  # nothing logged on the happy path


def test_extract_page_info_api_error_logs_and_raises(session, captured_log, monkeypatch):
    def _boom(*a, **k):
        raise anthropic.AnthropicError("model exploded")

    monkeypatch.setattr(uploader, "vision_json", _boom)
    session["username"] = "alice"

    with pytest.raises(PageExtractionError) as excinfo:
        extract_page_info(b"img", client=object(), book=_FakeBook(),
                          page_number=5, flow=ExtractionErrorLog.FLOW_RECONSTRUCTION)

    # The error was logged with the right context and the exception carries it.
    assert len(captured_log) == 1
    logged = captured_log[0]
    assert logged["book_id"] == "the_gruffalo"
    assert logged["book_title"] == "The Gruffalo"
    assert logged["page_number"] == 5
    assert logged["error_type"] == "AnthropicError"
    assert "model exploded" in logged["error_message"]
    assert logged["username"] == "alice"
    assert logged["flow"] == "reconstruction"
    assert excinfo.value.error_type == "AnthropicError"


def test_extract_page_info_parse_failure_logs_and_raises(session, captured_log, monkeypatch):
    # vision_json returns (None, raw) when the reply couldn't be parsed as JSON.
    monkeypatch.setattr(
        uploader, "vision_json", lambda *a, **k: (None, "not json at all"),
    )

    with pytest.raises(PageExtractionError) as excinfo:
        extract_page_info(b"img", client=object(), book=_FakeBook(),
                          page_number=1, flow="batch")

    assert len(captured_log) == 1
    assert captured_log[0]["error_type"] == ExtractionErrorLog.ERROR_PARSE
    assert "not json at all" in captured_log[0]["error_message"]
    assert excinfo.value.error_type == ExtractionErrorLog.ERROR_PARSE


def test_extract_page_info_no_reply_logs_and_raises(session, captured_log, monkeypatch):
    # vision_json returns (None, "") when there was no text block at all.
    monkeypatch.setattr(uploader, "vision_json", lambda *a, **k: (None, ""))

    with pytest.raises(PageExtractionError):
        extract_page_info(b"img", client=object(), page_number=1)

    assert captured_log[0]["error_type"] == ExtractionErrorLog.ERROR_PARSE
