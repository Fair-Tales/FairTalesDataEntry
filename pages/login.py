import streamlit as st
from utilities import page_layout, clear_page_history, authenticate_user, is_authenticated, get_admin, get_user, send_confirmation_email
from text_content import Terms, Alerts
from streamlit_option_menu import option_menu


def confirm(username, password):
    result = authenticate_user(username, password)
    if result == "ok":
        st.session_state['authentication_status'] = True
        st.session_state['username'] = username
        st.session_state.pop('unconfirmed_username', None)
        if get_admin(username):
            st.session_state['admin'] = True
        st.switch_page("./pages/user_home.py")
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
            reset_email = st.text_input("Enter your email address", key='reset_email')
            if st.button("Request password reset"):
                if reset_email.strip():
                    # TODO: implement full token-based reset flow
                    st.info(
                        "Password reset is not yet automated. Please email "
                        "dataentry.kidsbooks@gmail.com with your username and we will "
                        "reset your password manually."
                    )
                else:
                    st.warning("Please enter your email address.")

    else:
        st.header("Register")
        st.markdown(
            Terms.archivist_user_terms
        )
        accept_terms = st.checkbox("Accept")

        if accept_terms:
            st.switch_page("./pages/register_user.py")