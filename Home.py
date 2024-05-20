import streamlit as st
st.set_page_config(
    page_title="Home",
    initial_sidebar_state="collapsed"
)
from utilities import hide, is_authenticated, FirestoreWrapper, authenticate_user, author_entry_to_name
from text_content import Terms
from data_structures import Author, Book

# TODO:make better use of st-pages to show/hide and use icons: https://github.com/blackary/st_pages?tab=readme-ov-file
# TODO: fix this Arrow table issue https://discuss.streamlit.io/t/applying-automatic-fixes-for-column-types-to-make-the-dataframe-arrow-compatible/52717/2
# TODO: add options menu; https://discuss.streamlit.io/t/streamlit-option-menu-is-a-simple-streamlit-component-that-allows-users-to-select-a-single-item-from-a-list-of-options-in-a-menu/20514

# TODO: refactor utility methods to classes for conciseness (including authentication stuff).
# TODO: add different redirect to admin_home if user is admin (options to edit, download, validate data)
# TODO: migrate all text to use text_content module (including form content - use forms.py)
# TODO: add logout and 'remember me'
# TODO: Add password retrieval and reset (and allow other user info to be changed?)
# TODO: schedule user database backup?
# TODO: add confirmation check before some actions (e..g. cancel book entry): https://www.aprime.io/streamlit-tutorial-dynamic-confirmation-modals-session-state/
# TODO: improve email address validation

# TODO: add data protection statement to T&Cs.

# TODO: either move publisher and illustrator creation to later on (easier on computer), or simplify them: just name?

# TODO: add 'Home' option to return to user home at any time
# TODO: remove navigation menu (use options_menu for login or register) and default to collapse menu on all pages

# TODO: Implement auto save?

# TODO: finish implementing review_my_books: show selected

# TODO: update author class so that Book stores an author instance and uses properties w/ setters to handle author name, selection and reference access

# TODO: either expand Book.safe_cast and move to utilities, or remove pandas usage in FiresotreWarrper

# TODO: replace check_user_exists function with better solution as in FirestoreWrapper.document_exists

# TODO: add QR code for photo upload?

# TODO: add functionality to skip photo upload page (or choose to change photos) if photos were already uploaded
# TODO: update login so that it can redirect to a different page after success e.g. to upload photos on phone

# TODO: check book title is unique

# TODO: implement last updated. !!! And only save Book (or other object) if they have been modified !!!

# TODO: check Nonetype not subscriptable error on enter_text page. And change layout according to image dimensions.

# TODO: add options menu when selected book to review/edit

# TODO: replace auhtor_dict and book_dict with cached retrieval methods (will not scale to v. large database but OK for now)

# TODO: move save and add_to_dict to registration method of data structure base (and ensure only called once - not when editing).


def login():
    st.title("Login")
    username = st.text_input("Email", value="", key='login_email')
    password = st.text_input("Password", type="password", value="", key='login_password')
    if st.button("Login"):
        if authenticate_user(username, password):
            st.session_state['authentication_status'] = True
            st.session_state['username'] = username
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


def initialise():
    st.session_state['firestore'] = FirestoreWrapper(auth=True)
    st.session_state['current_book'] = Book()
    st.session_state['author'] = Author()
    st.session_state['active_form_to_confirm'] = None

    firestore = FirestoreWrapper(auth=False)
    st.session_state['author_dict'] = {
        author_entry_to_name(author): author.reference
        for author in
        firestore.get_all_documents_stream(collection='authors')
    }
    st.session_state['book_dict'] = {
        book.to_dict()['title']: book.reference
        for book in
        firestore.get_all_documents_stream(collection='books')
    }


def main():
    st.sidebar.title("Navigation")
    choice = st.sidebar.radio("Select an option:", ["Login", "Register"])

    if choice == "Login":
        login()
    elif choice == "Register":
        terms_and_conditions()


if __name__ == "__main__":

    initialise()
    hide()

    if is_authenticated():
        st.switch_page("./pages/user_home.py")
    main()

