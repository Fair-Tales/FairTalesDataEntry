"""Page-level tests for the validation heartbeat lifecycle (#234 / #235).

``pages/validation.py`` writes a durable "active" heartbeat
(``validation_active_at`` / ``validation_active_by``) when a validator OPENS a
book, so its owner cannot reopen it mid-review (#200). The #234 bug was that the
heartbeat was never RELEASED when the validator left, so a brief peek blocked the
owner for the whole activity window. These tests drive ``render_review`` against
in-memory fakes (no network, no real Streamlit runtime, no Firestore/S3/secrets)
and assert that:

* Back-to-list clears the heartbeat fields (and the session throttle entry).
* Approve clears them too.
* The soft mutual-exclusion warning (#235) is read from the FRESHLY-LOADED book
  BEFORE this session stamps its own heartbeat over ``validation_active_by``.

The complementary PURE decision helpers (``other_active_validator``, the
shortened activity window) are covered in ``tests/test_review_books_reopen.py``.
"""

from datetime import datetime, timedelta, timezone

import streamlit as st
import utilities


class _AttrDict(dict):
    """session_state stand-in supporting both item and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class _FakeSecrets(dict):
    def __contains__(self, _key):
        return False


class _FakeDoc:
    def __init__(self, exists=True, data=None):
        self.exists = exists
        self._data = data or {}

    def to_dict(self):
        return self._data


class _FakeFirestore:
    def __init__(self, doc):
        self._doc = doc

    def get_by_reference(self, collection, document_ref):
        return self._doc

    def username_to_doc_ref(self, username):
        return f"users/{username}"


# ---------------------------------------------------------------------------
# Import-time shim.
#
# pages/validation.py runs check_authentication_status(), the team-tier gate,
# page_layout(...) and the render dispatch unconditionally at import. Neutralise
# the utilities used at module top BEFORE importing (``from utilities import``
# binds these names into the page), and route the dispatch through the cheap
# render_review early-exit (a fake book doc that does not exist -> pop + rerun).
# Everything is restored immediately afterwards; each test installs its own
# fakes below.
# ---------------------------------------------------------------------------
_real_secrets = st.secrets
_real_session_state = st.session_state
_real_rerun = st.rerun
_real_check = utilities.check_authentication_status
_real_layout = utilities.page_layout
_real_team = utilities.is_team_or_above

st.secrets = _FakeSecrets()
st.session_state = _AttrDict(
    authentication_status=True,
    _validation_book_id="__import__",
    firestore=_FakeFirestore(_FakeDoc(exists=False)),
)
st.rerun = lambda *a, **k: None
utilities.check_authentication_status = lambda *a, **k: None
utilities.page_layout = lambda *a, **k: None
utilities.is_team_or_above = lambda: True
try:
    import pages.validation as validation  # noqa: E402
finally:
    st.secrets = _real_secrets
    st.session_state = _real_session_state
    st.rerun = _real_rerun
    utilities.check_authentication_status = _real_check
    utilities.page_layout = _real_layout
    utilities.is_team_or_above = _real_team


# ---------------------------------------------------------------------------
# Fakes for driving render_review.
# ---------------------------------------------------------------------------
class _Rerun(Exception):
    """Sentinel to unwind out of render_review at the st.rerun() call."""


class FakeBook:
    """Plain-attribute Book stand-in. Writes just set the attribute (the real
    write-through would persist them); the test inspects the attributes."""

    def __init__(self, db_object=None, *, active_by=None, active_at=-1):
        self.title = "The Test Book"
        self.document_id = "book_x"
        self.entered_by = None
        self.validated = False
        self.validated_by = None
        self.validation_active_by = active_by
        self.validation_active_at = active_at


class _DummyTab:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSt:
    """Minimal Streamlit stand-in recording the calls render_review makes."""

    def __init__(self, session_state, true_button_key):
        self.session_state = session_state
        self._true_button_key = true_button_key
        self.warnings = []
        self.successes = []

    # widgets / output
    def button(self, label, key=None):
        return key == self._true_button_key

    def tabs(self, labels):
        return [_DummyTab() for _ in labels]

    def warning(self, msg):
        self.warnings.append(msg)

    def success(self, msg):
        self.successes.append(msg)

    def header(self, *a, **k):
        pass

    def caption(self, *a, **k):
        pass

    def write(self, *a, **k):
        pass

    def subheader(self, *a, **k):
        pass

    def divider(self, *a, **k):
        pass

    def rerun(self, *a, **k):
        raise _Rerun()


def _run_render_review(monkeypatch, *, true_button_key, book, throttle_seed=True):
    """Drive validation.render_review with fakes; returns the FakeSt used.

    Raises nothing — the _Rerun sentinel thrown at st.rerun() is swallowed."""
    session_state = _AttrDict(
        _validation_book_id=book.document_id,
        username="chris@example.com",
        firestore=_FakeFirestore(_FakeDoc(exists=True, data={})),
    )
    if throttle_seed:
        # Pretend a heartbeat was written earlier this session, so we can assert
        # the clear pops the throttle entry.
        session_state["_validation_heartbeat"] = {book.document_id: 1.0}

    fake_st = FakeSt(session_state, true_button_key)
    monkeypatch.setattr(validation, "st", fake_st)
    monkeypatch.setattr(validation, "Book", lambda db_object=None: book)
    # The editors are exercised elsewhere; stub them so the approve path (after
    # the tabs) is reachable without Firestore.
    monkeypatch.setattr(validation, "metadata_editor", lambda b: None)
    monkeypatch.setattr(validation, "page_text_editor", lambda b: None)
    monkeypatch.setattr(validation, "characters_editor", lambda b: None)
    # Heartbeat throttle: with a real 30s throttle and a fresh session store the
    # stamp fires on open; keep the real helper so the stamp actually happens.
    try:
        validation.render_review()
    except _Rerun:
        pass
    return fake_st


# ---------------------------------------------------------------------------
# (a) Back-to-list releases the heartbeat.
# ---------------------------------------------------------------------------
def test_back_to_list_clears_heartbeat(monkeypatch):
    book = FakeBook()
    fake_st = _run_render_review(
        monkeypatch, true_button_key="validation_back_to_list_button", book=book
    )
    assert book.validation_active_at == -1
    assert book.validation_active_by is None
    # Throttle entry dropped so re-opening re-stamps immediately.
    assert book.document_id not in fake_st.session_state["_validation_heartbeat"]
    # Left the review.
    assert "_validation_book_id" not in fake_st.session_state


# ---------------------------------------------------------------------------
# (b) Approve releases the heartbeat.
# ---------------------------------------------------------------------------
def test_approve_clears_heartbeat(monkeypatch):
    book = FakeBook()
    fake_st = _run_render_review(
        monkeypatch, true_button_key="validation_approve_button", book=book
    )
    assert book.validated is True
    assert book.validated_by == "users/chris@example.com"
    assert book.validation_active_at == -1
    assert book.validation_active_by is None
    assert book.document_id not in fake_st.session_state["_validation_heartbeat"]
    assert "_validation_book_id" not in fake_st.session_state


# ---------------------------------------------------------------------------
# (#235) The mutual-exclusion warning is read BEFORE this session stamps its own
# heartbeat — a fresh FOREIGN heartbeat warns; our own / a stale one does not.
# ---------------------------------------------------------------------------
def test_foreign_live_heartbeat_warns(monkeypatch):
    now = datetime.now(timezone.utc)
    book = FakeBook(active_by="martha@example.com", active_at=now - timedelta(minutes=2))
    fake_st = _run_render_review(
        monkeypatch, true_button_key="validation_back_to_list_button", book=book
    )
    assert any("martha@example.com" in w for w in fake_st.warnings)


def test_stale_foreign_heartbeat_does_not_warn(monkeypatch):
    now = datetime.now(timezone.utc)
    book = FakeBook(
        active_by="martha@example.com",
        active_at=now - timedelta(minutes=utilities.VALIDATION_ACTIVITY_WINDOW_MINUTES + 5),
    )
    fake_st = _run_render_review(
        monkeypatch, true_button_key="validation_back_to_list_button", book=book
    )
    assert fake_st.warnings == []


def test_own_heartbeat_does_not_warn(monkeypatch):
    now = datetime.now(timezone.utc)
    book = FakeBook(active_by="chris@example.com", active_at=now - timedelta(minutes=1))
    fake_st = _run_render_review(
        monkeypatch, true_button_key="validation_back_to_list_button", book=book
    )
    assert fake_st.warnings == []
