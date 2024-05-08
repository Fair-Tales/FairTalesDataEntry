from google.cloud import firestore
import streamlit as st
from google.cloud.firestore_v1 import FieldFilter
from google.oauth2 import service_account
from st_pages import hide_pages
import pandas as pd
import json


# TODO: refactor utility methods to classes for conciseness.
# TODO: fix this Arrow table issue https://discuss.streamlit.io/t/applying-automatic-fixes-for-column-types-to-make-the-dataframe-arrow-compatible/52717/2


def is_authenticated():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    return st.session_state['authentication_status']


def check_authetication_status():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    if not is_authenticated():
        st.info("Please login.")
        st.stop()


def hide():
    hide_pages([
        'confirm', 'user_home', 'add_book', 'account_settings', 'confirm_entry',
        'add_character', 'add_author', 'book_data_entry', 'enter_text'
    ])


def author_entry_to_name(entry):
    """
    Helper method converts an author entry from the Firestore database
    to a readable string as 'forename surname'.
    """
    author = entry.to_dict()
    return ' '.join([author['forename'], author['surname']])


# TODO: is this still needed?
class CredentialsWrapper:
    """
    Wrapper class to handle getting credential dictionary from
    Firestore for use in streamlit-authenticator.
    """

    def __init__(self, firestore_key):
        self.firestore_key = firestore_key

    def credentials(self):
        db = firestore.Client.from_service_account_json(self.firestore_key)
        users = db.collection("users").stream()

        credentials_dict = {
            'usernames':
                {
                    user.id: {
                        'password': user.to_dict()['password'],
                        'logged_in': False
                    }
                    for user in users
                }
        }
        return credentials_dict

    def update_credentials(self):
        """
        Streamlit-authenticator wants us to update everything by just overwriting the config
        file by dumping the new version from memory. This does not seem ideal or safe!

        This method just updates any fields in the database that have been changed for the current user.
        """
        pass


class FirestoreWrapper:
    """
    Wrapper class to handle interacting with
    Firestore database (searching, querying, entering new data).
    """

    def __init__(self):
        self.firestore_key = json.loads(st.secrets["firestore_key"])

    def connect(self):
        creds = service_account.Credentials.from_service_account_info(self.firestore_key)
        return firestore.Client(credentials=creds, project="sawdataentry")

    def single_field_search(self, collection, field, contains_string):
        db = self.connect()

        results = (
            db.collection(collection)
                .where(filter=FieldFilter(field, ">=", contains_string))
                .where(filter=FieldFilter(field, "<=", contains_string + 'z'))
                .stream()
        )

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_reference(self, collection, document_ref):
        db = self.connect()
        doc_ref = db.collection(collection).document(document_ref)
        return doc_ref.get()

    def get_all_documents_stream(self, collection):
        db = self.connect()
        return db.collection(collection).stream()


# TODO: check that required fields (e.g. book title) are not blank
# TODO: populate form with current metadata/previoulsy entered form data f select edit
# TODO: fix warnings in table display (arrows?)
class FormConfirmation:
    """
    Class with helper methods to handle form confirmation and routing
    based on form type.
    """

    forms = {
        'new_book': 'confirm_new_book',
        'new_author': 'confirm_new_author',
        'new_character': 'confirm_new_character'
    }

    @classmethod
    def display_confirmation(cls, session_metadata):

        st.dataframe(st.session_state[session_metadata], use_container_width=True)
        col1, col2 = st.columns(2)
        confirm_button = col1.button("Confirm")
        edit_button = col2.button("Edit")

        return confirm_button, edit_button

    @classmethod
    def confirm_new_book(cls):
        confirm_button, edit_button = cls.display_confirmation('book_metadata')

        if confirm_button:
            if st.session_state.book_metadata['author'] == "None of these (create a new author).":
                st.switch_page("./pages/add_author.py")

            if st.session_state.book_metadata['publisher'] == "None of these (create a new publisher).":
                st.warning("Publisher creation not implemented yet!")

        if edit_button:
            st.switch_page("./pages/add_book.py")

    @classmethod
    def confirm_new_author(cls):
        confirm_button, edit_button = cls.display_confirmation('author_details')

        if confirm_button:
            st.switch_page("./pages/book_data_entry.py")

        if edit_button:
            st.switch_page("./pages/add_author.py")

    @classmethod
    def confirm_new_character(cls):
        confirm_button, edit_button = cls.display_confirmation('character_details')

        if confirm_button:
            st.switch_page("./pages/book_data_entry.py")

        if edit_button:
            st.switch_page("./pages/add_character.py")
