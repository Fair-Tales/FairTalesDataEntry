import secrets
from datetime import datetime, timezone

import streamlit as st
from google.api_core import exceptions as gapi_exceptions
from google.cloud import firestore

from utilities import FirestoreWrapper, page_layout, hash_password
from text_content import PasswordReset
from Home import ensure_session

# NOTE: This page is intentionally reachable WHILE LOGGED OUT.  Do NOT add
# check_authentication_status() here — a user resetting a forgotten password is
# not authenticated, and (per #88) the auth guard would switch_page to login
# before the reset logic could run.  Like confirm.py, this is a public page that
# acts only on a valid token+user match.

page_layout()

st.title(PasswordReset.page_title)

token = st.query_params.get('token')
user = st.query_params.get('user')

# Guard missing query params rather than letting attribute access raise.
if not token or not user:
    st.error(PasswordReset.invalid_link)
    st.stop()

db = FirestoreWrapper().connect_user(auth=False)
user_ref = db.collection("users").document(user)

try:
    user_doc = user_ref.get()
except gapi_exceptions.GoogleAPIError as error:
    st.error(PasswordReset.reset_failed)
    st.exception(error)
    st.stop()

user_data = user_doc.to_dict() if user_doc.exists else None
if user_data is None:
    st.error(PasswordReset.invalid_link)
    st.stop()

stored_token = user_data.get('reset_token')
expiry = user_data.get('reset_token_expiry')

# Constant-time comparison; bool(stored_token) guards a cleared/missing token so
# an empty stored value can never match an empty supplied token.
token_matches = bool(stored_token) and secrets.compare_digest(
    str(stored_token), str(token)
)

if not token_matches:
    st.error(PasswordReset.invalid_link)
    st.stop()


def _is_expired(expiry_value):
    """Return True if the stored expiry is missing or in the past.

    Firestore returns timezone-aware timestamps, but coerce any naive value to
    UTC defensively so the comparison never raises on a naive/aware mismatch.
    """
    if expiry_value is None:
        return True
    if expiry_value.tzinfo is None:
        expiry_value = expiry_value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expiry_value


if _is_expired(expiry):
    st.error(PasswordReset.expired_link)
    st.stop()

# Token is valid and unexpired.  Initialise the session the way Home.py does on
# first load — but WITHOUT its switch_page("login") — so that when control returns
# to Home.py after this page runs, the 'initialised' guard is already set and the
# fresh-load redirect to login does not fire before the user can use the form.
# (Mirrors qr_landing.py, the other interactive logged-out deep-link page.)
if 'initialised' not in st.session_state:
    st.session_state['initialised'] = True
    st.session_state['admin'] = False
    ensure_session()

# Token is valid and unexpired — let the user set a new password.
with st.form("reset_password_form"):
    new_password = st.text_input(
        PasswordReset.new_password_label, type="password", value="",
        key="reset_password_new_input"
    )
    confirm_password = st.text_input(
        PasswordReset.confirm_password_label, type="password", value="",
        key="reset_password_confirm_input"
    )
    submitted = st.form_submit_button(PasswordReset.submit_button_text, key="reset_password_submit_button")

if submitted:
    if not new_password or not confirm_password:
        st.warning(PasswordReset.blank_password)
    elif new_password != confirm_password:
        st.warning(PasswordReset.passwords_do_not_match)
    else:
        try:
            user_ref.update({
                'password': hash_password(new_password),
                # Invalidate the single-use token (and its expiry) immediately.
                'reset_token': firestore.DELETE_FIELD,
                'reset_token_expiry': firestore.DELETE_FIELD,
            })
        except gapi_exceptions.GoogleAPIError as error:
            st.error(PasswordReset.reset_failed)
            st.exception(error)
        else:
            st.success(PasswordReset.reset_success)
