import streamlit as st
import secrets
from datetime import datetime, timedelta, timezone
from utilities import (
    page_layout, clear_page_history, authenticate_user, is_authenticated,
    get_role, get_user, send_confirmation_email, send_password_reset_email,
    FirestoreWrapper, ROLE_ARCHIVIST, ROLE_ADMIN,
)
from text_content import Terms, Alerts, PasswordReset, Login
from data_structures import Book, Author, Publisher, Illustrator
from streamlit_option_menu import option_menu
from cookie_auth import (
    set_remember_cookie, clear_remember_cookie, remember_me_available, RESTORED_FLAG,
    JUST_LOGGED_OUT_FLAG,
)

# Validity window for an emailed password-reset link.
PASSWORD_RESET_VALIDITY = timedelta(hours=1)


def confirm(username, password, remember=False):
    result = authenticate_user(username, password)
    if result == "ok":
        st.session_state['authentication_status'] = True
        st.session_state['username'] = username
        st.session_state.pop('unconfirmed_username', None)
        # Resolve the three-tier role (#83) and store it on the session. Keep the
        # legacy 'admin' flag in sync so existing admin-gated pages and the
        # sidebar Admin link keep working unchanged.
        role = get_role(username)
        st.session_state['role'] = role
        st.session_state['admin'] = (role == ROLE_ADMIN)
        # Persist a signed, expiring (7-day) cookie so the session survives a page
        # reload or a server restart (#111). No-op when the box was unticked or no
        # cookie_signing_key secret is configured.
        if remember:
            set_remember_cookie(username)
            # Defer navigation: the CookieManager only writes the cookie when its
            # component renders at the END of this run; st.switch_page would abort
            # the run before that, so the cookie would never persist. Flag the
            # redirect and let this run finish — the component write triggers a
            # rerun, and the authenticated branch below then sends the user home.
            st.session_state['_post_login_redirect'] = True
            return
        st.switch_page("./pages/landing.py")
    elif result == "not_confirmed":
        # Password was correct but account not yet confirmed.  Store the
        # username so the resend button below the form can use it.
        st.session_state['unconfirmed_username'] = username
    else:
        # Bad credentials — clear any previous unconfirmed state so the
        # resend option is not shown for a different (or non-existent) account.
        st.session_state.pop('unconfirmed_username', None)
        st.error(Alerts.invalid_credentials)


def _resend_confirmation(username):
    """Look up the user's stored token and re-send the confirmation email."""
    user = get_user(username)
    if user:
        user_data = user.to_dict()
        send_confirmation_email(
            user_data['username'],
            user_data['username'],
            user_data['confirmation_token'],
            user_data['name'],
        )
        st.success(Alerts.confirmation_email_resent)

def _request_password_reset(email):
    """Generate, store and email a password-reset token for ``email``.

    Security: to avoid account enumeration the caller always shows the same
    acknowledgement regardless of whether an account exists, so this function
    silently returns when no matching user is found.  A cryptographically random,
    URL-safe token is stored on the user document together with an expiry; the
    reset page validates both before allowing a password change.
    """
    user = get_user(email)
    if user is None:
        return

    user_data = user.to_dict()
    reset_token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + PASSWORD_RESET_VALIDITY

    db = FirestoreWrapper().connect_user(auth=False)
    db.collection("users").document(user_data['username']).update({
        'reset_token': reset_token,
        'reset_token_expiry': expiry,
    })
    send_password_reset_email(
        user_data['username'],
        user_data['username'],
        reset_token,
        user_data['name'],
    )


# Session keys that are shared infrastructure (not user-specific) and must survive
# a logout: the Firestore client and the cached lookup dicts, plus the first-load
# 'initialised' flag. Everything else is per-user working state.
_LOGOUT_KEEP = {
    'firestore', 'initialised',
    'author_dict', 'publisher_dict', 'illustrator_dict',
    'book_dict', 'character_dict',
}


def logout():
    # Clear the persistent remember-me cookie (#111) BEFORE wiping session state,
    # so a signed-out user is not silently re-authenticated on the next reload.
    # Done first because the cookie component is read from session_state, which the
    # wipe loop below clears.
    clear_remember_cookie()
    # Wipe ALL per-session state except the shared infrastructure above, then
    # re-seed the empty working entities. Without this, one user's in-progress
    # state — e.g. a validator's open book review (`_validation_book_id`), a
    # half-entered book (`current_book`), or stale widget values — leaks into the
    # next login on a shared browser (the validation stale-data bug).
    for key in list(st.session_state.keys()):
        if key not in _LOGOUT_KEEP:
            del st.session_state[key]
    st.session_state['authentication_status'] = False
    st.session_state['username'] = ""
    st.session_state['role'] = ROLE_ARCHIVIST
    st.session_state['admin'] = False
    st.session_state['current_book'] = Book()
    st.session_state['author'] = Author()
    st.session_state['publisher'] = Publisher()
    st.session_state['illustrator'] = Illustrator()
    st.session_state['active_form_to_confirm'] = None
    # Defeat the Sign-Out vs remember-me race (#125): clear_remember_cookie() above
    # deletes the cookie via the ASYNC CookieManager, but restore_session_from_cookie()
    # reads SYNCHRONOUSLY from st.context.cookies, so the st.rerun() below would
    # otherwise re-read the not-yet-expired request cookie and re-authenticate —
    # making Sign Out a no-op while 'Remember me' is active. This one-shot flag
    # survives the rerun (it is in-session state) and is consumed by restore on the
    # next run, which then skips re-authenticating. Set AFTER the wipe loop above so
    # the wipe cannot delete it; it is not in _LOGOUT_KEEP because it must not
    # persist beyond the single post-logout rerun.
    st.session_state[JUST_LOGGED_OUT_FLAG] = True
    clear_page_history()
    st.rerun()


page_layout()

if is_authenticated():
    # If the user was just re-authenticated from a remember-me cookie on a hard
    # reload (#111), send them straight to their home page rather than showing the
    # sign-out prompt. A user who navigated here deliberately while logged in (no
    # restore flag) still sees the sign-out view below.
    if st.session_state.pop(RESTORED_FLAG, False) or \
            st.session_state.pop('_post_login_redirect', False):
        st.switch_page("./pages/landing.py")
    username = st.session_state['username']
    st.title(Login.sign_out_title)
    st.text(Login.signed_in_as.format(username=username))
    st.text(Login.sign_out_prompt)
    confirmed = st.button(Login.sign_out_button, key="login_sign_out_button")
    if confirmed:
        logout()

else:
    st.title(Login.sign_in_title)
    selected = option_menu("", options = [Login.menu_login, Login.menu_register], orientation="horizontal")

    if selected == Login.menu_login:
        st.header(Login.login_header)
        with st.form('LoginForm'):
            username = st.text_input(Login.email_label, value="", key='login_email').lower()
            password = st.text_input(Login.password_label, type="password", value="", key='login_password')
            # "Remember me" persistent login (#111). Only offered when a signing
            # key is configured; otherwise the feature is disabled and the box is
            # hidden so login behaves exactly as before.
            remember = False
            if remember_me_available():
                remember = st.checkbox(
                    Login.remember_me_checkbox,
                    value=False,
                    key="login_remember_me_checkbox",
                    help=Login.remember_me_help,
                )
            confirmed = st.form_submit_button(label=Login.confirm_button, key="login_submit_button")
            if confirmed:
                confirm(username, password, remember)

        # Show the "not confirmed" warning and resend button only when the last
        # login attempt was a correct-password / unconfirmed-account case.
        if st.session_state.get('unconfirmed_username'):
            st.warning(Alerts.account_not_confirmed)
            if st.button(Login.resend_button, key="login_resend_button"):
                _resend_confirmation(st.session_state['unconfirmed_username'])

        with st.expander(Login.forgot_password_expander):
            reset_email = st.text_input(
                PasswordReset.request_email_label, key='reset_email'
            ).lower().strip()
            if st.button(PasswordReset.request_button_text, key="login_reset_request_button"):
                if reset_email:
                    _request_password_reset(reset_email)
                    # Always show the same acknowledgement, whether or not an
                    # account exists, to avoid leaking which emails are registered.
                    st.info(PasswordReset.request_acknowledgement)
                else:
                    st.warning(PasswordReset.request_blank_email)

    else:
        st.header(Login.register_header)
        st.markdown(
            Terms.archivist_user_terms
        )
        accept_terms = st.checkbox(Login.accept_checkbox, key="login_accept_terms_checkbox")

        if accept_terms:
            st.switch_page("./pages/register_user.py")