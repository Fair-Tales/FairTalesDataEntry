import streamlit as st
st.set_page_config(
    page_title="Home",
    initial_sidebar_state="expanded"
)
from utilities import hide, is_authenticated, FirestoreWrapper, authenticate_user
from text_content import Terms

# TODO:make better use of st-pages to show/hide and use icons: https://github.com/blackary/st_pages?tab=readme-ov-file

# TODO: refactor utility methods to classes for conciseness (including authentication stuff).
# TODO: fix this Arrow table issue https://discuss.streamlit.io/t/applying-automatic-fixes-for-column-types-to-make-the-dataframe-arrow-compatible/52717/2

# TODO: remove credentials wrapper utility?
# TODO: refactor some login/registration code from here
# TODO: add different redirect to admin_home if user is admin

# TODO: add options menu; https://discuss.streamlit.io/t/streamlit-option-menu-is-a-simple-streamlit-component-that-allows-users-to-select-a-single-item-from-a-list-of-options-in-a-menu/20514

# TODO: migrate all text to use text_content module

# TODO: ensure username/email unique
# TODO: what genders when registering?
# TODO: add logout and 'remember me'

# TODO: Add password retrieval and reset (and allow other user info to be changed?)
# TODO: make fields blank when switching register/login and vice verca
# TODO: schedule user database backup?
# TODO: currently just using username as email. Happy with this?

# TODO: add confirmation check before some actions (e..g. cancel book entry): https://www.aprime.io/streamlit-tutorial-dynamic-confirmation-modals-session-state/


def login():
    st.title("Login")
    username = st.text_input("Email", value="")
    password = st.text_input("Password", type="password", value="")
    if st.button("Login"):
        if authenticate_user(username, password):
            st.session_state['authentication_status'] = True
            st.switch_page("./pages/user_home.py")
        else:
            st.error("Invalid credentials.")


def terms_and_conditions():
    st.text(
        Terms.archivist_user_terms
    )
    accept_terms = st.checkbox("Accept")

    if accept_terms:
        st.switch_page("./pages/register_user.py")


def main():
    st.sidebar.title("Navigation")
    choice = st.sidebar.radio("Select an option:", ["Login", "Register"])

    st.session_state['firestore'] = FirestoreWrapper()

    if choice == "Login":
        login()
    elif choice == "Register":
        terms_and_conditions()


if __name__ == "__main__":
    hide()
    if is_authenticated():
        st.switch_page("./pages/user_home.py")
    main()

