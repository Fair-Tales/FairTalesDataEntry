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
import logging
from datetime import datetime, timezone, timedelta

import streamlit as st
import extra_streamlit_components as stx
from streamlit.errors import StreamlitSecretNotFoundError

from utilities import get_user, get_role, ROLE_ADMIN, normalize_username

logger = logging.getLogger(__name__)

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
# One-shot session_state flag set by ``pages/login.logout()`` to defeat the
# Sign-Out vs remember-me race (#125). Restore reads the cookie SYNCHRONOUSLY from
# ``st.context.cookies`` (request headers) while logout deletes it via the ASYNC
# CookieManager, so the ``st.rerun()`` after Sign Out would otherwise re-read the
# not-yet-expired request cookie and re-authenticate — making Sign Out a no-op
# while 'Remember me' is active. The flag lives only in in-memory ``session_state``
# so it survives that same-session rerun; ``restore_session_from_cookie`` consumes
# it once (pop) and skips, so Sign Out actually sticks.
JUST_LOGGED_OUT_FLAG = "_just_logged_out"


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
    # One-shot guard for the Sign-Out vs remember-me race (#125). logout() sets
    # JUST_LOGGED_OUT_FLAG just before its st.rerun(); because the cookie is
    # deleted via the ASYNC CookieManager but read here SYNCHRONOUSLY from
    # st.context.cookies, that rerun would otherwise still see the request cookie
    # and re-authenticate, turning Sign Out into a no-op. Consume the flag (pop) and
    # skip restore exactly once so the user lands on — and stays on — the login
    # page. Checked first so the flag is always cleared on the post-logout rerun.
    if st.session_state.pop(JUST_LOGGED_OUT_FLAG, False):
        return
    if st.session_state.get("authentication_status"):
        return
    if not remember_me_available():
        return
    # Read the cookie SYNCHRONOUSLY from the incoming request headers
    # (st.context.cookies) rather than the CookieManager component. The component
    # only delivers values after an async frontend round-trip, so on a fresh hard
    # reload it returns nothing on the first run and the user is bounced to login
    # before it hydrates. st.context.cookies is populated from the request and is
    # available immediately on the first run.
    #
    # RESIDUAL RACE (#125, accepted): the one-shot flag only lives in this
    # session's memory, so it cannot cover a *different* run that has no flag — most
    # notably a hard reload (new session) issued in the brief window before the
    # async cookie delete propagates to the browser. In that window st.context.cookies
    # still carries the stale (signature-valid, unexpired) token and the session
    # would be restored. We deliberately do NOT re-confirm against the CookieManager
    # copy here: the component returns nothing on its first run (the very reason this
    # function reads st.context.cookies), so gating on it would break the legitimate
    # cold-reload restore that #111 added. The exposure is a sub-second timing edge
    # on a shared browser; a user who needs a guaranteed local sign-out should not
    # immediately hard-reload. Revisit if a server-side session/revocation list lands.
    cookies = getattr(st.context, "cookies", None)
    raw = cookies.get(COOKIE_NAME) if cookies else None
    if not raw:
        return
    username = _verify_token(raw)
    if username is None:
        return
    # Normalize (#129 shared helper) defensively: the payload was written from
    # an already-normalized session username, but normalizing again on the way
    # in guarantees session_state['username'] is always the canonical form
    # even against an older cookie minted before this fix.
    username = normalize_username(username)
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
    # Server-side record of a reconnect-with-empty-session event (#185): this path
    # only runs when a script rerun found NO authenticated session (a websocket
    # reconnect / fresh script run after the server dropped the session) and we
    # transparently rebuilt it from the remember-me cookie. Logging it lets pilot
    # "controls vanished" reports be correlated with real disconnects. Correlates
    # with the ``_remember_restored`` marker consumed on the next page render.
    logger.info(
        "Session restored from remember-me cookie for user=%s "
        "(reconnect with empty session_state, #185).",
        username,
    )
