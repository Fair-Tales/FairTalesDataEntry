import streamlit as st
from utilities import (
    is_authenticated,
    FirestoreWrapper,
    load_author_dict,
    load_publisher_dict,
    load_illustrator_dict,
    load_book_dict,
    load_character_dict,
)
from data_structures import Author, Book, Illustrator, Publisher

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
# DONE (#51): skip photo upload / replace photos handled in page_photo_upload.py when photos already uploaded
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
# TODO: add 'initialise_session_state' function and if something (like current_book) is not present, redirect to user_home.
# TODO: schedule database backup
# TODO: add data protection statement to T&Cs.
# TODO: add diagram of how to take photos
# DONE (#51): portrait orientation fixed via EXIF transpose at upload (uploader.py) and display (enter_text.py) - verify with real portrait photos

def initialise():
    st.session_state['firestore'] = FirestoreWrapper(auth=True)
    st.session_state['current_book'] = Book()
    st.session_state['author'] = Author()
    st.session_state['publisher'] = Publisher()
    st.session_state['illustrator'] = Illustrator()
    st.session_state['active_form_to_confirm'] = None

    # Lookup dicts are served from cached resource loaders (see utilities.py)
    # rather than re-streaming every collection on each session init (issue #53).
    # We shallow-copy each cached dict into session_state so that in-session
    # mutations (a freshly registered author/book/etc. added by the
    # FormConfirmation.confirm_new_* methods or Character.register) only affect
    # this session and never poison the shared cache. Those writes also call the
    # matching ``load_*_dict.clear()`` so subsequent sessions re-read from
    # Firestore — preserving write-through freshness.
    st.session_state['author_dict'] = dict(load_author_dict())
    st.session_state['publisher_dict'] = dict(load_publisher_dict())
    st.session_state['illustrator_dict'] = dict(load_illustrator_dict())
    st.session_state['book_dict'] = dict(load_book_dict())
    st.session_state['character_dict'] = dict(load_character_dict())

def navigate_pages():
    
    pages = {
        "Menu":[
            st.Page("./pages/login.py", title='Sign Out'),
            st.Page("./pages/account_settings.py", title='Account Settings'),
            st.Page("./pages/landing.py", title='Home'),
            st.Page("./pages/user_home.py", title='Enter Data'),
            st.Page("./pages/priority_books.py", title='Books We Need'),
            st.Page("./pages/report_feedback.py", title='Report a Bug / Feature'),
        ],
        "Other pages":[
            st.Page("./pages/add_author.py"),
            st.Page("./pages/add_illustrator.py"),
            st.Page("./pages/add_publisher.py"),
            st.Page("./pages/add_book.py"),
            st.Page("./pages/add_book_photos.py"),
            st.Page("./pages/add_character.py"),
            st.Page("./pages/book_data_entry.py"),
            st.Page("./pages/book_edit_home.py"),
            st.Page("./pages/confirm_entry.py"),
            st.Page("./pages/confirm.py"),
            st.Page("./pages/reset_password.py"),
            st.Page("./pages/enter_text.py"),
            st.Page("./pages/page_photo_upload.py"),
            st.Page("./pages/qr_landing.py"),
            st.Page("./pages/register_user_done.py"),
            st.Page("./pages/register_user.py"),
            st.Page("./pages/review_my_books.py"),
            st.Page("./pages/uploader.py"),
            st.Page("./pages/donate.py"),
            st.Page("./pages/results_dashboard.py"),
        ]
    }

    if 'admin' in st.session_state and st.session_state['admin']:
        pages["Menu"].append(st.Page("./pages/admin.py", title='Admin'))
        pages["Other pages"].append(st.Page("./pages/validation.py"))

    st.navigation(pages, position="hidden").run()

if __name__ == "__main__":

    navigate_pages()
    if 'initialised' not in st.session_state:
        st.session_state['initialised'] = True
        st.session_state['admin'] = False
        initialise()
        st.switch_page("./pages/login.py")