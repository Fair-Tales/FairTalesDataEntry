import streamlit as st
st.set_page_config(
    page_title="Home",
    initial_sidebar_state="collapsed"
)
from utilities import hide, is_authenticated, FirestoreWrapper, authenticate_user, author_entry_to_name
from text_content import Terms
from data_structures import Author, Book

# TODO: make better use of st-pages to show/hide and use icons: https://github.com/blackary/st_pages?tab=readme-ov-file
# TODO: fix this Arrow table issue https://discuss.streamlit.io/t/applying-automatic-fixes-for-column-types-to-make-the-dataframe-arrow-compatible/52717/2
# TODO: add options menu; https://discuss.streamlit.io/t/streamlit-option-menu-is-a-simple-streamlit-component-that-allows-users-to-select-a-single-item-from-a-list-of-options-in-a-menu/20514

# TODO: refactor utility methods to classes for conciseness (including authentication stuff).
# TODO: add different redirect to admin_home if user is admin (options to edit, download, validate data)
# TODO: migrate all text to use text_content module (including form content - use forms.py)
# TODO: add logout and 'remember me' buttons
# TODO: Add password retrieval and reset (and allow other user info to be changed?)
# TODO: add confirmation check before some actions (e.g. cancel book entry): https://www.aprime.io/streamlit-tutorial-dynamic-confirmation-modals-session-state/
# TODO: improve email address validation
# TODO: add 'Home' option to return to user home at any time
# TODO: either expand Book.safe_cast and move to utilities, or remove pandas usage in FiresotreWarrper
# TODO: replace check_user_exists function with better solution as in FirestoreWrapper.document_exists
# TODO: add functionality to skip photo upload page (or choose to change photos) if photos were already uploaded
# TODO: And change layout according to image dimensions.
# TODO: replace auhtor_dict and book_dict with cached retrieval methods (will not scale to v. large database but OK for now)
# TODO: add 'help' instructions throughout
# TODO: write and use 'user' data structure (to handle changing account details and updating db)
# TODO: add option to enter book 'themes' (for later use: disability, race...). Also allow comment on book?
# TODO: double check add_author functionality (where is current_author added to session state?)
# TODO: optimise to reduce Firestore read/write. And profile to see what else is slowing it down. (See Book form - updated)
# TODO: change publisher and illustrator to be data_structures/firestore docs?
# TODO: remove navigation menu (use options_menu for login or register) and default to collapse menu on all pages
# TODO: dynamically choose textt - whether to add or edit book, and 'cancel new' or 'cancel edit'
# TODO: add splitlines and replace tab: do this when download tsv (otherwise want to preserve entry)
# TODO: remove cookie secrets
# TODO: why so slow after page upload complete?
# TODO: add edits characters and aliases to book edit options menu...
# TODO: change formatting for hover instructions (so they stand out better)
# TODO: (add previous_page to session state?) so that we can use an in-app  back button
# TODO: delete character or alias
# TODO: refactor base data structure so that it doesn't use 'db_object'
# TODO: updated computer page once phone photos uploaded?
# TODO: add example page photos to instructions
# TODO: add back button
# TODO: add view ad edit characters/aliases

## BEFORE STUDENTS:
# TODO: add author search
# TODO: use photos uploaded flag?
# TODO: add timeout to QR link?
# TODO: add our previous book titles (and collections?)
# TODO: delete junk from databases!!

# TODO: Add Alias lets you select from all characters not just book ones!

# TODO: add whitespace striping from names entered?
# TODO: add 'initialise_session_state' function and if something (like current_book) is not present, redirect to user_home.
# TODO: adding alias form did not clear.
# TODO: edit character? (e.g. if they got gender wrong)
# TODO: schedule database backup
# TODO: add data protection statement to T&Cs.
# TODO: add diagram of how to take photos
# TODO: check orientation of portrait images - not working atm.
# TODO: change register/login menu


def login():
    st.title("Login")
    username = st.text_input("Email", value="", key='login_email').lower()
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
    st.session_state['character_dict'] = {
        character.to_dict()['name']: character.reference
        for character in
        firestore.get_all_documents_stream(collection='characters')
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

