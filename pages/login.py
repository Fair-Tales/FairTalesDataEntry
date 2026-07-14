import streamlit as st
import secrets
from datetime import datetime, timedelta, timezone
from utilities import (
    page_layout, clear_page_history, authenticate_user, is_authenticated,
    get_role, get_user, send_confirmation_email, send_password_reset_email,
    FirestoreWrapper, ROLE_ARCHIVIST, ROLE_ADMIN, render_header_bar,
    normalize_username,
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
    # Normalize here too (defense in depth, #129 shared helper) so
    # session_state['username'] — which every later doc-ref/lookup keys off —
    # is always the canonical lowercase form, even if this is ever called with
    # un-normalized input.
    username = normalize_username(username)
    # Residual #174 root cause (empirically confirmed, 2026-07-14): when a
    # browser/password-manager AUTOFILLS the login fields, the values can be
    # painted into the DOM without the input events Streamlit needs to sync
    # widget state — so the FIRST Confirm click submits empty strings ("no
    # result"/invalid credentials), while the second click (after the rerun
    # re-registers the fields) succeeds. Guard the empty submit with a specific,
    # actionable message instead of a misleading "Invalid credentials", and skip
    # the pointless Firestore lookup + bcrypt check. The autocomplete attributes
    # on the form inputs below are the actual fix (they make Chrome/password
    # managers fill via real input events); this guard is the safety net.
    if not username or not password:
        st.warning(Login.missing_fields)
        return
    result = authenticate_user(username, password)
    if result == "ok":
        st.session_state['authentication_status'] = True
        st.session_state['username'] = username
        st.session_state.pop('unconfirmed_username', None)
        # A successful explicit login ends the signed-out state: release the
        # session-persistent just-logged-out guard (#125/#207) so cookie
        # restore works normally again for the rest of this session.
        st.session_state.pop(JUST_LOGGED_OUT_FLAG, None)
        # Resolve the three-tier role (#83) and store it on the session. Keep the
        # legacy 'admin' flag in sync so existing admin-gated pages and the
        # sidebar Admin link keep working unchanged.
        role = get_role(username)
        st.session_state['role'] = role
        st.session_state['admin'] = (role == ROLE_ADMIN)
        # Route EVERY successful login through the top-of-page authenticated
        # redirect instead of switching pages here (#139).
        st.session_state['_post_login_redirect'] = True
        # Persist a signed, expiring (7-day) cookie so the session survives a page
        # reload or a server restart (#111). No-op when the box was unticked or no
        # cookie_signing_key secret is configured.
        #
        # TRUE root cause of the recurring "Confirm needs two clicks" bug (#174):
        # this handler runs INSIDE ``with st.form('LoginForm')``. Writing the
        # remember-me cookie here means the CookieManager ``set`` component renders
        # inside the form — and Streamlit SUPPRESSES a component's value-change
        # rerun until the form is next submitted. The remember path deliberately
        # issues no ``st.rerun()`` (a rerun would abort the component's cookie
        # write) and instead trusted the component write to trigger the redirect
        # run. Inside a form that trigger never fires, so the deferred redirect sat
        # idle until the user clicked Confirm a SECOND time. #139 fixed the
        # non-remember path with an explicit ``st.rerun()`` but wrongly assumed the
        # remember path "already worked"; #144 then made Remember-me the DEFAULT,
        # exposing the always-broken path to every login — hence the regression.
        #
        # Fix: NEVER write the cookie from inside the form. Both paths now set
        # session state and issue a single deterministic ``st.rerun()`` (safe — no
        # cookie component is rendered in this run, so nothing is aborted). For the
        # remember path we only stash the username; the actual cookie write is
        # deferred to the top-of-page authenticated block below, OUTSIDE the form,
        # where the ``set`` component's write DOES trigger the follow-on redirect
        # run cleanly. Single click for both remember-on and remember-off.
        if remember:
            st.session_state['_pending_remember_cookie'] = username
        st.rerun()
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
    user = get_user(normalize_username(username))
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
    user = get_user(normalize_username(email))
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
    # Defeat the Sign-Out vs remember-me race (#125/#207). The flag now persists
    # for the remainder of this session (restore PEEKS it; a new login pops it)
    # because st.context.cookies keeps serving the connection-time cookie for
    # the rest of the websocket session. Set AFTER the wipe loop above so the
    # wipe cannot delete it.
    st.session_state[JUST_LOGGED_OUT_FLAG] = True
    # The actual browser-cookie delete is DEFERRED to the next run (#207): the
    # CookieManager delete is executed by a component iframe, and the
    # st.rerun() below would replace the page before that iframe ever ran —
    # empirically the cookie then SURVIVED sign-out and a later reload
    # re-authenticated the user (a shared-device hazard). The login page
    # renders the delete on the next run and st.stop()s so it completes —
    # the exact mirror of the #174 deferred cookie WRITE.
    st.session_state['_pending_remember_clear'] = True
    clear_page_history()
    st.rerun()


page_layout()
render_header_bar()

if is_authenticated():
    # Deferred remember-me cookie write (#174). A single-click login that ticked
    # "Remember me" reaches here (authenticated, redirect pending) with the
    # username stashed. Writing the cookie HERE — at the top of the page, OUTSIDE
    # the login st.form — lets the CookieManager ``set`` component trigger its
    # value-change rerun normally (a form would suppress it, which is what caused
    # the two-click bug). We must NOT redirect on this same run: st.switch_page
    # would abort the run before the component dispatches its write, so the cookie
    # would never persist (#111). Write, show a brief notice, and st.stop(); the
    # component write triggers the next run, on which (no pending cookie) the
    # _post_login_redirect branch below sends the user home. Net: one Confirm
    # click for both remember-on and remember-off.
    pending_remember = st.session_state.pop('_pending_remember_cookie', None)
    if pending_remember:
        set_remember_cookie(pending_remember)
        st.info(Login.signing_in)
        st.stop()
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
    # Deferred remember-me cookie DELETE (#207), mirroring the deferred write
    # above: logout() cannot render the delete component itself (its st.rerun()
    # would unmount the component's iframe before the delete JS ran, leaving
    # the cookie in the browser — verified empirically). Render it here, on the
    # first signed-out run, and stop; the component's value-change rerun brings
    # the user to the normal login form below with the cookie actually gone.
    if st.session_state.pop('_pending_remember_clear', False):
        clear_remember_cookie()
        st.info(Login.signing_out)
        st.stop()
    st.title(Login.sign_in_title)
    selected = option_menu("", options = [Login.menu_login, Login.menu_register], orientation="horizontal")

    if selected == Login.menu_login:
        st.header(Login.login_header)
        with st.form('LoginForm'):
            # autocomplete attributes (#174): Streamlit defaults password inputs
            # to autocomplete="new-password", which makes Chrome/password
            # managers treat this as a REGISTRATION form — saved credentials get
            # painted in without the input events Streamlit needs, so the first
            # Confirm submits empty values and only the second click works.
            # "username" + "current-password" mark this as a LOGIN form, making
            # browsers fill via real input events that sync widget state.
            username = normalize_username(st.text_input(
                Login.email_label, value="", key='login_email',
                autocomplete="username",
            ))
            password = st.text_input(
                Login.password_label, type="password", value="",
                key='login_password', autocomplete="current-password",
            )
            # "Remember me" persistent login (#111). Only offered when a signing
            # key is configured; otherwise the feature is disabled and the box is
            # hidden so login behaves exactly as before.
            remember = False
            if remember_me_available():
                # Default ON (#144): Streamlit Cloud clears in-memory session_state
                # on every app restart (idle/1GB-memory pressure), so a session-only
                # login drops the user after minutes. Persisting by default keeps
                # archivists signed in across those restarts; they can untick it on a
                # shared device.
                remember = st.checkbox(
                    Login.remember_me_checkbox,
                    value=True,
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
            reset_email = normalize_username(st.text_input(
                PasswordReset.request_email_label, key='reset_email'
            ))
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