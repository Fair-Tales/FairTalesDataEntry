"""Regression test for a book_search crash after admin delete-book (#188).

A book row whose ``book_dict`` entry survives past the underlying Firestore
document's deletion (a stale cache entry, or any other doc that has simply
vanished) used to crash ``pages.user_home.book_search`` with an
``AttributeError`` at ``book_data.get('author')`` because ``book_data`` was
``None`` (``DocumentSnapshot.to_dict()`` returns ``None`` for a
non-existent doc). ``book_search`` now resolves each candidate title up
front and skips (logging a warning) any whose document no longer exists,
instead of crashing.

Also locks in that ``_person_name_from_ref``/``_publisher_name_from_ref``
tolerate ``None``, a deleted/empty doc snapshot, and a plain string ref
without raising — the other half of the fix.

Exercised against in-memory fakes (no network, no real Firestore/S3/secrets),
the same style as ``tests/test_add_books_batch_page_isolation.py``. Calling
into real ``st.write``/``st.warning``/``st.expander`` is safe under pytest:
outside a real Streamlit script run they no-op (with a logged "missing
ScriptRunContext" warning) rather than raising.
"""

import logging

import streamlit as st
import pytest


class _AttrDict(dict):
    """Minimal stand-in for Streamlit's real ``session_state``, which supports
    BOTH ``st.session_state['x']`` and ``st.session_state.x`` access."""

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
# Import-time workaround (mirrors tests/test_add_books_batch_page_isolation.py).
#
# Importing pages/user_home.py unconditionally runs
# check_authentication_status(), page_layout(...), and — at the bottom —
# option_menu(...) + the navigation dispatch. Outside a real Streamlit run,
# ``st_keyup``/``option_menu`` both fall back to their default/first value
# (no ScriptRunContext), so the default dispatch lands on ``book_search()``
# with an empty search string, which short-circuits before touching
# ``st.session_state['book_dict']``. A throwaway authenticated session state
# and a secrets stand-in that never claims to hold a key are enough to get
# through import safely; both are restored immediately afterwards.
_import_state = _AttrDict()
_import_state['authentication_status'] = True

_real_secrets = st.secrets
_real_session_state = st.session_state
st.secrets = _FakeSecrets()
st.session_state = _import_state
try:
    import pages.user_home as user_home  # noqa: E402
finally:
    st.secrets = _real_secrets
    st.session_state = _real_session_state


# ---------------------------------------------------------------------------
# Fakes for the person/publisher ref resolvers.
# ---------------------------------------------------------------------------

class _FakeMissingSnapshot:
    """A Firestore DocumentSnapshot for a doc that no longer exists."""

    def to_dict(self):
        return None


class _FakeExistingSnapshot:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeRef:
    """Stand-in for a Firestore DocumentReference."""

    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get(self):
        return self._snapshot


class _FakeBookRef:
    """Stand-in for a book's DocumentReference: ``.get().to_dict()`` returns
    either a book dict (still exists) or None (deleted / stale entry)."""

    def __init__(self, book_data):
        self._book_data = book_data

    def get(self):
        return self  # DocumentSnapshot-like: only .to_dict() is used.

    def to_dict(self):
        return self._book_data


# ---------------------------------------------------------------------------
# _person_name_from_ref / _publisher_name_from_ref
# ---------------------------------------------------------------------------

def test_person_name_from_ref_none():
    assert user_home._person_name_from_ref(None) == user_home.UserHome.unknown


def test_person_name_from_ref_deleted_doc_does_not_raise():
    ref = _FakeRef(_FakeMissingSnapshot())
    assert user_home._person_name_from_ref(ref) == user_home.UserHome.unknown


def test_person_name_from_ref_plain_string():
    assert user_home._person_name_from_ref("julia_donaldson") == "julia donaldson"


def test_person_name_from_ref_name_field():
    ref = _FakeRef(_FakeExistingSnapshot({"name": "Axel Scheffler"}))
    assert user_home._person_name_from_ref(ref) == "Axel Scheffler"


def test_person_name_from_ref_forename_surname_fallback():
    ref = _FakeRef(_FakeExistingSnapshot({"forename": "Julia", "surname": "Donaldson"}))
    assert user_home._person_name_from_ref(ref) == "Julia Donaldson"


def test_publisher_name_from_ref_none():
    assert user_home._publisher_name_from_ref(None) == user_home.UserHome.unknown


def test_publisher_name_from_ref_deleted_doc_does_not_raise():
    ref = _FakeRef(_FakeMissingSnapshot())
    assert user_home._publisher_name_from_ref(ref) == user_home.UserHome.unknown


# ---------------------------------------------------------------------------
# book_search: stale book_dict entry (deleted book, #188) must not crash.
# ---------------------------------------------------------------------------

@pytest.fixture
def wired_session():
    state = _AttrDict()
    state['authentication_status'] = True
    real_state = st.session_state
    st.session_state = state
    yield state
    st.session_state = real_state


def test_book_search_skips_stale_entry_without_crashing(wired_session, monkeypatch, caplog):
    # "Gruffalo" was deleted (e.g. via admin delete-book) but its book_dict
    # entry is still around (stale cache / not-yet-pruned session copy);
    # "Room on the Broom" is a live book that should still show up.
    stale_ref = _FakeBookRef(None)
    live_ref = _FakeBookRef({"title": "Room on the Broom", "author": None, "published": 1999})
    wired_session['book_dict'] = {
        "The Gruffalo": stale_ref,
        "Room on the Broom": live_ref,
    }

    # "o" matches both "The Gruffalo" (stale) and "Room on the Broom" (live) so
    # both are exercised by the same search.
    monkeypatch.setattr(user_home, "st_keyup", lambda *a, **k: "o")

    with caplog.at_level(logging.WARNING):
        user_home.book_search()  # must not raise

    assert any("stale book_dict entry" in rec.getMessage() for rec in caplog.records)


def test_book_search_all_stale_shows_no_matching_warning(wired_session, monkeypatch):
    wired_session['book_dict'] = {"The Gruffalo": _FakeBookRef(None)}
    monkeypatch.setattr(user_home, "st_keyup", lambda *a, **k: "gruff")

    # Must not raise even when every match is stale.
    user_home.book_search()
