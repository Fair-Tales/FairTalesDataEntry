"""Unit tests for the #207/#125 remember-me hardening in ``cookie_auth``.

Playwright verification of the full browser flow lives in the repro notes
(handoff); these tests pin the pure/monkeypatchable logic:

- signed-token round-trip, tamper and expiry rejection;
- restore falls back to the CookieManager component snapshot when the request
  headers carry no cookie (#207 proxy/cross-site hardening);
- the just-logged-out guard persists for the session (peek, not pop) so a
  post-sign-out rerun can never re-authenticate from the stale request cookie;
- a TRANSIENT user-lookup failure skips restore WITHOUT destroying the cookie,
  while a clean "user gone" still clears it;
- clear_remember_cookie renders the delete component even when this run's
  getAll snapshot does not list the cookie;
- source-level: logout defers the browser delete to the login page's
  stop-and-wait run (the delete iframe must outlive the run, #207).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import streamlit as st
from google.api_core import exceptions as google_exceptions

import cookie_auth


KEY = "test-signing-key"


# ---------------------------------------------------------------------------
# Token round-trip.
# ---------------------------------------------------------------------------

@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setattr(cookie_auth, "_signing_key", lambda: KEY)


def _token(username="alice@example.com", delta=timedelta(days=7)):
    return cookie_auth._make_token(
        username, datetime.now(timezone.utc) + delta, KEY
    )


def test_token_roundtrip(signing_key):
    assert cookie_auth._verify_token(_token()) == "alice@example.com"


def test_tampered_token_rejected(signing_key):
    token = _token()
    payload, _, sig = token.rpartition(".")
    assert cookie_auth._verify_token(f"{payload}x.{sig}") is None
    assert cookie_auth._verify_token(f"{payload}.{'0' * len(sig)}") is None


def test_expired_token_rejected(signing_key):
    assert cookie_auth._verify_token(_token(delta=timedelta(seconds=-5))) is None


# ---------------------------------------------------------------------------
# restore_session_from_cookie.
# ---------------------------------------------------------------------------

class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class _FakeContext:
    def __init__(self, cookies):
        self.cookies = cookies


class _FakeManager:
    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})
        self.deleted = []

    def delete(self, name, key="delete"):
        self.deleted.append((name, key))
        del self.cookies[name]


@pytest.fixture
def restore_env(monkeypatch, signing_key):
    """Wire a fake session/context/user-db; returns the mutable environment."""
    state = _AttrDict()
    monkeypatch.setattr(st, "session_state", state)
    env = {
        "state": state,
        "context_cookies": {},
        "manager": None,
        "user_exists": True,
        "user_error": None,
        "cleared": [],
    }
    monkeypatch.setattr(
        cookie_auth.st, "context", _FakeContext(env["context_cookies"]),
        raising=False,
    )
    monkeypatch.setattr(cookie_auth, "_cookie_manager", lambda: env["manager"])

    def fake_get_user(username):
        if env["user_error"] is not None:
            raise env["user_error"]
        return {"username": username} if env["user_exists"] else None

    monkeypatch.setattr(cookie_auth, "get_user", fake_get_user)
    monkeypatch.setattr(cookie_auth, "get_role", lambda u: "archivist")
    monkeypatch.setattr(
        cookie_auth, "clear_remember_cookie",
        lambda: env["cleared"].append(True),
    )
    return env


def test_restore_from_request_cookie(restore_env):
    restore_env["context_cookies"][cookie_auth.COOKIE_NAME] = _token()
    cookie_auth.restore_session_from_cookie()
    assert restore_env["state"]["authentication_status"] is True
    assert restore_env["state"]["username"] == "alice@example.com"
    assert restore_env["state"][cookie_auth.RESTORED_FLAG] is True


def test_restore_falls_back_to_component_snapshot(restore_env):
    """#207: request headers carry no cookie (stripping proxy / cross-site
    navigation) — the CookieManager's document.cookie snapshot must be used."""
    restore_env["manager"] = _FakeManager({cookie_auth.COOKIE_NAME: _token()})
    cookie_auth.restore_session_from_cookie()
    assert restore_env["state"].get("authentication_status") is True


def test_no_cookie_anywhere_means_no_restore(restore_env):
    restore_env["manager"] = _FakeManager({})
    cookie_auth.restore_session_from_cookie()
    assert restore_env["state"].get("authentication_status") is None


def test_just_logged_out_guard_persists_across_runs(restore_env):
    """#125/#207: the guard is PEEKED, not popped — after Sign Out no rerun of
    this session may restore from the (stale) request cookie."""
    restore_env["context_cookies"][cookie_auth.COOKIE_NAME] = _token()
    restore_env["state"][cookie_auth.JUST_LOGGED_OUT_FLAG] = True
    for _ in range(3):  # every subsequent rerun, not just the first
        cookie_auth.restore_session_from_cookie()
        assert restore_env["state"].get("authentication_status") is None
    assert restore_env["state"][cookie_auth.JUST_LOGGED_OUT_FLAG] is True


def test_transient_user_lookup_failure_keeps_the_cookie(restore_env):
    """#207: a Firestore outage/quota error must not destroy the remember-me
    cookie; restore is skipped for this run only."""
    restore_env["context_cookies"][cookie_auth.COOKIE_NAME] = _token()
    restore_env["user_error"] = google_exceptions.ServiceUnavailable("quota")
    cookie_auth.restore_session_from_cookie()
    assert restore_env["state"].get("authentication_status") is None
    assert restore_env["cleared"] == []  # cookie left intact


def test_deleted_user_clears_the_cookie(restore_env):
    restore_env["context_cookies"][cookie_auth.COOKIE_NAME] = _token()
    restore_env["user_exists"] = False
    cookie_auth.restore_session_from_cookie()
    assert restore_env["state"].get("authentication_status") is None
    assert restore_env["cleared"] == [True]


# ---------------------------------------------------------------------------
# clear_remember_cookie.
# ---------------------------------------------------------------------------

def test_clear_renders_delete_even_when_snapshot_lacks_cookie(monkeypatch):
    manager = _FakeManager({})  # getAll snapshot doesn't list the cookie
    monkeypatch.setattr(cookie_auth, "_cookie_manager", lambda: manager)
    cookie_auth.clear_remember_cookie()
    assert manager.deleted == [(cookie_auth.COOKIE_NAME, "remember_delete")]


# ---------------------------------------------------------------------------
# Source-level locks on the login page's deferred delete (#207).
# ---------------------------------------------------------------------------

LOGIN_SRC = (Path(__file__).parent.parent / "pages" / "login.py").read_text()


def test_logout_defers_the_cookie_delete():
    logout_src = LOGIN_SRC[LOGIN_SRC.find("def logout"):LOGIN_SRC.find("page_layout()")]
    assert "clear_remember_cookie()" not in logout_src, (
        "logout() must not render the delete component itself — its st.rerun() "
        "unmounts the iframe before the delete JS runs (#207)"
    )
    assert "_pending_remember_clear" in logout_src


def test_login_page_renders_deferred_delete_then_stops():
    marker = LOGIN_SRC.find("st.session_state.pop('_pending_remember_clear'")
    assert marker != -1
    tail = LOGIN_SRC[marker:marker + 400]
    assert "clear_remember_cookie()" in tail
    assert "st.stop()" in tail
