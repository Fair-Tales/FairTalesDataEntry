import streamlit as st
from utilities import page_layout
from text_content import Terms
from streamlit_option_menu import option_menu
from utilities import authenticate_user, is_authenticated
from streamlit_js_eval import streamlit_js_eval

def confirm(username, password):
    #print("heya", username, password)
    if authenticate_user(username, password):
        st.session_state['authentication_status'] = True
        st.session_state['username'] = username
        st.switch_page("./pages/user_home.py")
    else:
        st.error("Invalid credentials.")

def logout():
    st.session_state['authentication_status'] = False
    st.session_state['username'] = ""
    streamlit_js_eval(js_expressions="parent.window.location.reload()")


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
            #st.title("Login")
            username = st.text_input("Email", value="", key='login_email').lower()
            password = st.text_input("Password", type="password", value="", key='login_password')
            confirmed = st.form_submit_button(label="Confirm")
            if confirmed:
                confirm(username, password)

    else:
        st.header("Register")
        st.markdown(
            Terms.archivist_user_terms
        )
        accept_terms = st.checkbox("Accept")

        if accept_terms:
            st.switch_page("./pages/register_user.py")
            #st.navigation([st.Page("./pages/login.py"),st.Page("./pages/register_user.py")], position="hidden").run()