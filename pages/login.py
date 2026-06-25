import streamlit as st
from utilities import page_layout
from text_content import Terms
from streamlit_option_menu import option_menu
from utilities import authenticate_user, is_authenticated, get_admin

def confirm(username, password):
    if authenticate_user(username, password):
        st.session_state['authentication_status'] = True
        st.session_state['username'] = username
        if get_admin(username):
            st.session_state['admin'] = True
        st.switch_page("./pages/user_home.py")
    else:
        st.error("Invalid credentials.")

def logout():
    st.session_state['authentication_status'] = False
    st.session_state['username'] = ""
    st.session_state['admin'] = False
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