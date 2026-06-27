import streamlit as st
import secrets
from datetime import datetime, timedelta, timezone
from utilities import (
    page_layout, clear_page_history, authenticate_user, is_authenticated,
    get_admin, get_user, send_confirmation_email, send_password_reset_email,
    FirestoreWrapper,
)
from text_content import Terms, Alerts, PasswordReset
from streamlit_option_menu import option_menu

# Validity window for an emailed password-reset link.
PASSWORD_RESET_VALIDITY = timedelta(hours=1)


def confirm(username, password):
    result = authenticate_user(username, password)
    if result == "ok":
        st.session_state['authentication_status'] = True
        st.session_state['username'] = username
        st.session_state.pop('unconfirmed_username', None)
        if get_admin(username):
            st.session_state['admin'] = True
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


def logout():
    st.session_state['authentication_status'] = False
    st.session_state['username'] = ""
    st.session_state['admin'] = False
    clear_page_history()
    st.rerun()


page_layout()

if is_authenticated():
    username = st.session_state['username']
    st.title("Sign Out")
    st.text(f"Currently signed in as {username}")
    st.text("Would you like to sign out?")
    confirmed = st.button("Sign Out")
    if confirmed:
        logout()

else:
    st.title("Sign In")
    selected = option_menu("", options = ["Login", 'Register'], orientation="horizontal")

    if selected == "Login":
        st.header("Login")
        with st.form('LoginForm'):
            username = st.text_input("Email", value="", key='login_email').lower()
            password = st.text_input("Password", type="password", value="", key='login_password')
            confirmed = st.form_submit_button(label="Confirm")
            if confirmed:
                confirm(username, password)

        # Show the "not confirmed" warning and resend button only when the last
        # login attempt was a correct-password / unconfirmed-account case.
        if st.session_state.get('unconfirmed_username'):
            st.warning(Alerts.account_not_confirmed)
            if st.button("Resend confirmation email"):
                _resend_confirmation(st.session_state['unconfirmed_username'])

        with st.expander("Forgot your password?"):
            reset_email = st.text_input(
                PasswordReset.request_email_label, key='reset_email'
            ).lower().strip()
            if st.button(PasswordReset.request_button_text):
                if reset_email:
                    _request_password_reset(reset_email)
                    # Always show the same acknowledgement, whether or not an
                    # account exists, to avoid leaking which emails are registered.
                    st.info(PasswordReset.request_acknowledgement)
                else:
                    st.warning(PasswordReset.request_blank_email)

    else:
        st.header("Register")
        st.markdown(
            Terms.archivist_user_terms
        )
        accept_terms = st.checkbox("Accept")

        if accept_terms:
            st.switch_page("./pages/register_user.py")