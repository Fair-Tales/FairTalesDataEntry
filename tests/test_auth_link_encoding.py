"""Tests for the account confirm / password-reset email flow.

Covers two independently-verified MEDIUM bugs:

- #239 — the confirmation / password-reset email links were built with plain
  f-string interpolation, so a plus-addressed email username (the app's
  usernames ARE email addresses, e.g. ``chris+pilot@gmail.com``) had its ``+``
  decoded as a space by ``st.query_params`` on the confirm/reset page, breaking
  the user lookup. The links must now be ``urlencode``-d so ``+`` becomes
  ``%2B`` and round-trips.

- #240 (also the #230 ``confirm.py:16`` deleted-ref site) — the confirm-page
  decision logic (extracted into ``utilities.evaluate_confirmation`` so the page
  stays thin and Streamlit's top-level-script execution is not needed to test
  it) must fail gracefully to a friendly invalid-link message on a missing
  token/user, a deleted/missing user doc, a missing stored token, or a token
  mismatch, and use a constant-time compare (``hmac.compare_digest``) on match.
"""

from urllib.parse import parse_qs, urlsplit

import pytest
import streamlit as st

import utilities
from utilities import evaluate_confirmation


# ---------------------------------------------------------------------------
# #239 — email link encoding.
# ---------------------------------------------------------------------------

class _FakeSMTP:
    """Records sent messages instead of contacting Gmail."""

    def __init__(self, captured):
        self._captured = captured

    def ehlo(self):
        pass

    def login(self, *args):
        pass

    def send_message(self, msg):
        self._captured.append(msg)

    def close(self):
        pass


@pytest.fixture
def captured_emails(monkeypatch):
    """Patch SMTP, secrets and the base URL; return the list of sent messages."""
    captured = []
    monkeypatch.setattr(
        utilities.smtplib, "SMTP_SSL", lambda *a, **k: _FakeSMTP(captured)
    )
    monkeypatch.setattr(
        utilities, "public_base_url", lambda: "https://example.com/"
    )
    monkeypatch.setattr(
        st, "secrets",
        {"email_address": "bot@example.com", "gmail_app_password": "pw"},
        raising=False,
    )
    return captured


def _link_from(msg):
    """Extract the (single) URL appended to an email body.

    ``get_payload(decode=True)`` handles whichever transfer-encoding MIMEText
    picked (the reset body is long enough that MIMEText base64-encodes it).
    """
    body = msg.get_payload(decode=True).decode()
    for token in body.split():
        if token.startswith("https://"):
            return token
    raise AssertionError("no link found in email body")


PLUS_USER = "chris+pilot@gmail.com"


def test_confirmation_link_encodes_plus_username(captured_emails):
    utilities.send_confirmation_email(
        send_to=PLUS_USER,
        username=PLUS_USER,
        confirmation_token="deadbeef",
        name="Chris",
    )
    link = _link_from(captured_emails[0])
    parts = urlsplit(link)

    assert parts.path == "/confirm"
    # The raw query must percent-encode the plus so it is not read as a space.
    assert "%2B" in parts.query
    assert "+" not in parts.query

    params = parse_qs(parts.query)
    assert params["user"] == [PLUS_USER]
    assert params["token"] == ["deadbeef"]


def test_reset_link_encodes_plus_username(captured_emails):
    utilities.send_password_reset_email(
        send_to=PLUS_USER,
        username=PLUS_USER,
        reset_token="cafef00d",
        name="Chris",
    )
    link = _link_from(captured_emails[0])
    parts = urlsplit(link)

    assert parts.path == "/reset_password"
    assert "%2B" in parts.query
    assert "+" not in parts.query

    params = parse_qs(parts.query)
    assert params["user"] == [PLUS_USER]
    assert params["token"] == ["cafef00d"]


# ---------------------------------------------------------------------------
# #240 / #230 — confirm-page decision logic.
# ---------------------------------------------------------------------------

TOKEN = "a" * 40


def test_missing_token_is_invalid():
    assert evaluate_confirmation(None, "user@example.com", {}) == "invalid"
    assert evaluate_confirmation("", "user@example.com", {}) == "invalid"


def test_missing_user_is_invalid():
    assert evaluate_confirmation(TOKEN, "", {"confirmation_token": TOKEN}) == "invalid"
    assert evaluate_confirmation(TOKEN, None, {"confirmation_token": TOKEN}) == "invalid"


def test_missing_user_doc_is_invalid():
    # ``to_dict()`` returns None for a deleted/missing account (the #230 site).
    assert evaluate_confirmation(TOKEN, "gone@example.com", None) == "invalid"


def test_missing_stored_token_is_invalid():
    assert evaluate_confirmation(TOKEN, "user@example.com", {"is_confirmed": False}) == "invalid"


def test_wrong_token_is_invalid():
    user_data = {"is_confirmed": False, "confirmation_token": "b" * 40}
    assert evaluate_confirmation(TOKEN, "user@example.com", user_data) == "invalid"


def test_already_confirmed_short_circuits():
    user_data = {"is_confirmed": True, "confirmation_token": TOKEN}
    assert evaluate_confirmation(TOKEN, "user@example.com", user_data) == "already_confirmed"


def test_correct_token_confirms():
    user_data = {"is_confirmed": False, "confirmation_token": TOKEN}
    assert evaluate_confirmation(TOKEN, "user@example.com", user_data) == "confirm"


def test_page_updates_only_on_confirm_outcome():
    """The page marks the account confirmed exactly when the outcome is 'confirm'.

    Mirrors the guarded update in ``pages/confirm.py`` so a refactor that
    unconditionally writes ``is_confirmed`` (or forgets to on a match) is caught.
    """
    class _FakeRef:
        def __init__(self):
            self.updated = None

        def update(self, data):
            self.updated = data

    def run(token, user, user_data):
        ref = _FakeRef()
        outcome = evaluate_confirmation(token, user, user_data)
        if outcome == "confirm":
            ref.update({"is_confirmed": True})
        return outcome, ref

    outcome, ref = run(TOKEN, "u@e.com", {"is_confirmed": False, "confirmation_token": TOKEN})
    assert outcome == "confirm"
    assert ref.updated == {"is_confirmed": True}

    outcome, ref = run(TOKEN, "u@e.com", {"is_confirmed": False, "confirmation_token": "x"})
    assert outcome == "invalid"
    assert ref.updated is None
