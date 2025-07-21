import streamlit as st
from utilities import is_authenticated, FirestoreWrapper, author_entry_to_name
from data_structures import Author, Book

# TODO: make better use of st-pages to show/hide and use icons: https://github.com/blackary/st_pages?tab=readme-ov-file
# TODO: fix this Arrow table issue https://discuss.streamlit.io/t/applying-automatic-fixes-for-column-types-to-make-the-dataframe-arrow-compatible/52717/2
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
# TODO: optimise to reduce Firestore read/write. And profile to see what else is slowing it down. (See Book form - updated)
# TODO: change publisher and illustrator to be data_structures/firestore docs?
# TODO: remove navigation menu (use options_menu for login or register) and default to collapse menu on all pages
# TODO: dynamically choose text - whether to add or edit book, and 'cancel new' or 'cancel edit'
# TODO: add split lines and replace tab: do this when download tsv (otherwise want to preserve entry)
# TODO: remove cookie secrets
# TODO: why so slow after page upload complete?
# TODO: add edit characters and aliases to book edit options menu...
# TODO: change formatting for hover instructions (so they stand out better)
# TODO: (add previous_page to session state?) so that we can use an in-app back button
# TODO: add in app back button
# TODO: allow delete character or alias
# TODO: refactor base data structure so that it doesn't use 'db_object'
# TODO: trigger update to computer page to notify once phone photos uploaded?
# TODO: add example page photos to instructions
# TODO: add methods to view and edit characters/aliases

# TODO: Migrate pilot study data to databases:
#  Not sure what is the easiest way to do this? But need to:
#    - add our previous book titles (and collections?)
#    - add characters
#    - add text data
#    - add pages photos
#   Should be possible to at least mostly automate this, but some things are new e.g. book themes

# Other improvements:
# TODO: add author search
# TODO: use photos uploaded flag?
# TODO: add timeout to QR link?
# TODO: delete development junk from databases!!
# TODO: Add Alias lets you select from all characters not just book ones!
# TODO: add whitespace striping from names entered?
# TODO: add 'initialise_session_state' function and if something (like current_book) is not present, redirect to user_home.
# TODO: adding alias form did not clear.
# TODO: schedule database backup
# TODO: add data protection statement to T&Cs.
# TODO: add diagram of how to take photos
# TODO: check orientation of portrait images - not working atm.

def initialise():
    st.session_state['firestore'] = FirestoreWrapper(auth=True)
    st.session_state['current_book'] = Book()
    st.session_state['author'] = Author()
    st.session_state['active_form_to_confirm'] = None

    firestore = FirestoreWrapper(auth=False)
    st.session_state['author_dict'] = {
        author_entry_to_name(author): author.reference
        for author in
        firestore.get_all_documents_stream(collection='Authors')
    }
    st.session_state['book_dict'] = {
        book.to_dict()['title'].lower().replace(" ", "_"): book.reference
        for book in
        firestore.get_all_documents_stream(collection='Book')
    }
    st.session_state['character_dict'] = {
        character.to_dict()['name']: character.reference
        for character in
        firestore.get_all_documents_stream(collection='Characters')
    }

def navigate_pages():
    pages = {
        "Menu":[
            st.Page("./pages/login.py", title='Login'),
            st.Page("./pages/account_settings.py", title='Account Settings'),
            st.Page("./pages/user_home.py", title='Home'),
        ],
        "Other pages":[
            st.Page("./pages/add_author.py"),
            st.Page("./pages/add_book.py"),
            st.Page("./pages/add_character.py"),
            st.Page("./pages/book_data_entry.py"),
            st.Page("./pages/book_edit_home.py"),
            st.Page("./pages/confirm_entry.py"),
            st.Page("./pages/confirm.py"),
            st.Page("./pages/enter_text.py"),
            st.Page("./pages/page_photo_upload.py"),
            st.Page("./pages/qr_landing.py"),
            st.Page("./pages/register_user_done.py"),
            st.Page("./pages/register_user.py"),
            st.Page("./pages/review_my_books.py"),
            st.Page("./pages/uploader.py"),
        ]
    }

    st.navigation(pages, position="sidebar").run()

if __name__ == "__main__":

    navigate_pages()
    if 'initialised' not in st.session_state:
        st.session_state['initialised'] = True
        initialise()
        st.switch_page("./pages/login.py")