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
from google.api_core import exceptions as google_exceptions
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
# Session flag set by ``pages/login.logout()`` to defeat the Sign-Out vs
# remember-me race (#125/#207). Restore reads the cookie SYNCHRONOUSLY from
# ``st.context.cookies`` — headers captured when the websocket connected, which
# keep carrying the deleted cookie for the REST of the session — while logout
# deletes it via the browser-side CookieManager component. The flag therefore
# persists for the whole remainder of the signed-out session (PEEKED, never
# popped, by ``restore_session_from_cookie``); only an explicit new login
# (``pages/login.confirm``) pops it. It lives only in in-memory
# ``session_state``, so a fresh session (reload) starts clean and reads fresh
# request cookies — by then the deferred delete has actually executed.
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
    # same_site="lax" (#207): Streamlit Cloud fronts every cold visit with a
    # cross-site auth bounce (app -> share.streamlit.io/-/auth/app -> app), and
    # links/QR codes arriving from other sites are cross-site top-level
    # navigations too. A SameSite=Strict cookie is NOT sent on those document
    # requests, which is exactly the deployed "refresh logs me out" failure
    # mode; Lax sends the cookie on top-level GET navigations while still
    # blocking cross-site subresource/POST sends. This mirrors Streamlit's own
    # ``streamlit_session`` cookie (Lax), and CSRF exposure is nil: the token
    # only re-establishes a read session (every mutation rides the websocket
    # session), is HMAC-signed, and never carries privileges (role is
    # re-resolved server-side on restore). ``secure`` is left unset so the
    # cookie also works over plain HTTP in local development (Streamlit Cloud
    # serves over HTTPS regardless).
    manager.set(
        COOKIE_NAME,
        token,
        key="remember_set",
        expires_at=expiry,
        same_site="lax",
    )


def clear_remember_cookie():
    """Render the delete-cookie component for the remember-me cookie.

    IMPORTANT (#207): the delete is executed by the component's IFRAME in the
    browser, so the script run that renders it must survive long enough for
    that iframe to load and run — an ``st.rerun()`` issued in the same run
    replaces the page before the delete JS executes and the cookie SURVIVES
    (empirically reproduced: sign-out left the cookie in the browser and a
    later reload silently re-authenticated the signed-out user). Callers must
    therefore render this on a run that ENDS with ``st.stop()`` and let the
    component's own value-change rerun continue the flow — the same deferred
    pattern as the #174 login cookie write. See ``pages/login.py``.
    """
    manager = _cookie_manager()
    if manager is None:
        return
    # Render the delete component even when this run's getAll snapshot does not
    # list the cookie (the snapshot can lag reality; deleting a cookie that is
    # already absent is a browser-side no-op). CookieManager.delete() ends with
    # ``del self.cookies[name]`` which would KeyError on a missing entry, so
    # seed the entry first instead of skipping the delete.
    manager.cookies.setdefault(COOKIE_NAME, None)
    manager.delete(COOKIE_NAME, key="remember_delete")


def restore_session_from_cookie():
    """Re-establish an authenticated session from a valid remember-me cookie.

    Runs once per rerun (from ``Home.py``) before any page body. Does nothing when
    the session is already authenticated, when remember-me is disabled, or when no
    valid cookie is present. On a valid cookie it re-resolves the role/admin flag
    from the Firestore user document (never trusting the cookie) and confirms the
    user still exists, then sets the session authentication state.
    """
    # Sign-Out vs remember-me guard (#125/#207). logout() sets
    # JUST_LOGGED_OUT_FLAG; because the cookie delete is executed by a browser
    # component while this function reads SYNCHRONOUSLY from st.context.cookies
    # (headers captured at connection time, which still carry the deleted
    # cookie for the REST of this websocket session), the flag must persist for
    # the whole remainder of the session — not just one rerun as before. A
    # PEEK (get), never a pop: after Sign Out, only an explicit new login
    # (pages/login.confirm pops the flag) may re-authenticate this session. A
    # fresh session (reload) reads fresh request cookies, by which time the
    # deferred delete (#207, see clear_remember_cookie) has executed.
    if st.session_state.get(JUST_LOGGED_OUT_FLAG, False):
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
        # Fallback (#207): read the CookieManager component's snapshot of
        # document.cookie. On a deployment where the request headers reach the
        # script without the cookie (proxy stripping, or a cross-site
        # navigation quirk in the Cloud auth bounce), the browser-side
        # component still sees it. The component returns nothing on its very
        # first run of a cold load — that case restores a rerun later, when the
        # component hydrates and triggers its value-change rerun (the login
        # page then redirects home via RESTORED_FLAG). The synchronous
        # st.context read above stays primary so the normal cold-reload restore
        # (#111) is still immediate.
        manager = _cookie_manager()
        if manager is not None:
            raw = (manager.cookies or {}).get(COOKIE_NAME)
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
    #
    # Transient-failure hardening (#207): a Firestore outage / exhausted quota
    # here must NOT destroy the user's remember-me cookie — only a CLEAN
    # "user does not exist" answer may clear it. On a transient error skip the
    # restore for this run (the user can retry / the next rerun retries) and
    # log it. GoogleAPIError covers the google-cloud client's call failures.
    try:
        user_exists = get_user(username) is not None
    except google_exceptions.GoogleAPIError as exc:
        logger.warning(
            "Session restore for user=%s skipped: user lookup failed "
            "transiently (%s). Cookie left intact.", username, exc,
        )
        return
    if not user_exists:
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
