"""Persistent "Remember me" login via a signed, expiring cookie (issue #111).

Authentication normally lives only in Streamlit's per-tab ``session_state``, so a
hard page reload or a server restart starts a fresh session and bounces the user
to the login form. When a user ticks "Remember me" at login we additionally write
a *signed, expiring* cookie that ``Home.py`` uses to re-establish the session on a
fresh script run.

SECURITY MODEL
- The cookie stores ONLY the username and an absolute expiry timestamp, plus an
  HMAC-SHA256 signature over those two values. The password is NEVER stored, nor
  is anything else sensitive.
- The signature is keyed by ``st.secrets["cookie_signing_key"]``. A tampered or
  forged cookie fails the constant-time signature check (``hmac.compare_digest``)
  and is rejected; an expired cookie is likewise rejected.
- The role / admin flag is NOT trusted from the cookie. On restore we re-resolve
  the role from the Firestore user document via ``get_role()`` and confirm the
  user still exists, so a stale or forged cookie can never escalate privileges
  (coordinates with the #83 role tiers).
- TTL is 7 days (Chris-approved).

DEPLOYMENT
The feature only activates when ``cookie_signing_key`` is present in
``st.secrets``. Add a random hex string to ``.streamlit/secrets.toml`` (and to
the Streamlit Cloud secrets) to enable it. Generate one with, e.g.:

    python -c "import secrets; print(secrets.token_hex(32))"

then add to ``.streamlit/secrets.toml``:

    cookie_signing_key = "<that-random-hex-string>"

If the secret is absent the feature disables cleanly: no cookie is written, no
restore is attempted, and login behaves exactly as before (session-only). No key
is ever invented or committed by this code.

COOKIE COMPONENT
Streamlit cannot set browser cookies natively, so we use the ``CookieManager``
from ``extra-streamlit-components`` — a maintained component that is ALREADY a
project dependency (the same package streamlit-authenticator builds on), which is
why it is preferred here over adding a new package. Its frontend runs a getAll
round-trip on construction, so it must be instantiated exactly once per script
run (see ``init_cookie_manager``).
"""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta

import streamlit as st
import extra_streamlit_components as stx
from streamlit.errors import StreamlitSecretNotFoundError

from utilities import get_user, get_role, ROLE_ADMIN

# Name of the browser cookie holding the signed remember-me token.
COOKIE_NAME = "fairtales_remember"
# Chris-approved persistent-session length.
REMEMBER_DURATION = timedelta(days=7)
# Name of the HMAC signing key looked up in ``st.secrets``.
SIGNING_KEY_SECRET = "cookie_signing_key"
# session_state slot caching the single per-run CookieManager component.
_COOKIE_MANAGER_KEY = "_cookie_manager"
# session_state flag set when a session was just restored from a cookie, so the
# login page can redirect a freshly-restored user home instead of showing the
# sign-out prompt.
RESTORED_FLAG = "_remember_restored"


def _signing_key():
    """Return the configured HMAC signing key, or ``None`` when not configured.

    Returns ``None`` — disabling the feature — when no secrets file exists or the
    ``cookie_signing_key`` entry is absent/empty.
    """
    try:
        key = st.secrets.get(SIGNING_KEY_SECRET)
    except StreamlitSecretNotFoundError:
        return None
    return key or None


def remember_me_available():
    """True when remember-me is enabled (a signing key is configured)."""
    return _signing_key() is not None


# ---------------------------------------------------------------------------
# Token signing / verification
# ---------------------------------------------------------------------------

def _b64encode(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


def _b64decode(text):
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("utf-8")).decode("utf-8")


def _sign(message, key):
    return hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _make_token(username, expiry, key):
    """Build a ``<payload>.<signature>`` token for ``username`` expiring at ``expiry``."""
    payload = {"u": username, "exp": int(expiry.timestamp())}
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def _verify_token(token):
    """Return the username from a valid, unexpired, correctly-signed token, else ``None``.

    Verifies the HMAC signature with a constant-time compare and checks the expiry
    timestamp. Any malformed / tampered / expired token returns ``None``.
    """
    key = _signing_key()
    if key is None or not token or "." not in token:
        return None
    payload_b64, _, signature = token.rpartition(".")
    expected = _sign(payload_b64, key)
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, UnicodeDecodeError):
        return None
    username = payload.get("u")
    expiry = payload.get("exp")
    if not username or not isinstance(expiry, int):
        return None
    if expiry <= int(datetime.now(timezone.utc).timestamp()):
        return None
    return username


# ---------------------------------------------------------------------------
# Cookie component lifecycle
# ---------------------------------------------------------------------------

def init_cookie_manager():
    """Instantiate the per-run CookieManager component (once per script run).

    Called once per rerun from ``Home.py`` before any page body executes. The
    instance is cached in ``session_state`` so the login/logout call sites reuse
    it (set/delete use distinct component keys) rather than re-instantiating it,
    which would raise a DuplicateWidgetID error. No-op (and clears any stale
    instance) when remember-me is disabled.
    """
    if not remember_me_available():
        st.session_state.pop(_COOKIE_MANAGER_KEY, None)
        return None
    manager = stx.CookieManager(key="cookie_manager")
    st.session_state[_COOKIE_MANAGER_KEY] = manager
    return manager


def _cookie_manager():
    """Return the CookieManager built for this run, or ``None`` if unavailable."""
    return st.session_state.get(_COOKIE_MANAGER_KEY)


def set_remember_cookie(username):
    """Write a signed, 7-day remember-me cookie for ``username``.

    No-op when remember-me is disabled or the cookie component is unavailable.
    Stores only the username + expiry + HMAC signature — never the password.
    """
    key = _signing_key()
    if key is None:
        return
    manager = _cookie_manager()
    if manager is None:
        return
    expiry = datetime.now(timezone.utc) + REMEMBER_DURATION
    token = _make_token(username, expiry, key)
    # same_site="strict" limits CSRF exposure; ``secure`` is left unset so the
    # cookie also works over plain HTTP in local development (Streamlit Cloud
    # serves over HTTPS regardless).
    manager.set(
        COOKIE_NAME,
        token,
        key="remember_set",
        expires_at=expiry,
        same_site="strict",
    )


def clear_remember_cookie():
    """Delete the remember-me cookie (called on Sign Out). No-op when absent."""
    manager = _cookie_manager()
    if manager is None:
        return
    # CookieManager.delete() does ``del self.cookies[name]`` and would KeyError if
    # the cookie is not currently known, so guard on membership first.
    if COOKIE_NAME in manager.cookies:
        manager.delete(COOKIE_NAME, key="remember_delete")


def restore_session_from_cookie():
    """Re-establish an authenticated session from a valid remember-me cookie.

    Runs once per rerun (from ``Home.py``) before any page body. Does nothing when
    the session is already authenticated, when remember-me is disabled, or when no
    valid cookie is present. On a valid cookie it re-resolves the role/admin flag
    from the Firestore user document (never trusting the cookie) and confirms the
    user still exists, then sets the session authentication state.
    """
    if st.session_state.get("authentication_status"):
        return
    if not remember_me_available():
        return
    manager = _cookie_manager()
    if manager is None:
        return
    raw = manager.get(COOKIE_NAME)
    if not raw:
        return
    username = _verify_token(raw)
    if username is None:
        return
    # Re-resolve from the database. Never trust a role baked into the cookie: a
    # forged/stale cookie must not be able to escalate privileges, and a deleted
    # user must not be restored.
    if get_user(username) is None:
        clear_remember_cookie()
        return
    role = get_role(username)
    st.session_state["authentication_status"] = True
    st.session_state["username"] = username
    st.session_state["role"] = role
    st.session_state["admin"] = role == ROLE_ADMIN
    st.session_state[RESTORED_FLAG] = True
