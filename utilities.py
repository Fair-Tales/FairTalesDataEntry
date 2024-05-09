from google.cloud import firestore
import streamlit as st
from google.cloud.firestore_v1 import FieldFilter
from google.oauth2 import service_account
from st_pages import hide_pages
import pandas as pd
import json
import bcrypt
import smtplib
from email.mime.text import MIMEText


def is_authenticated():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    return st.session_state['authentication_status']


def check_authentication_status():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    if not is_authenticated():
        st.info("Please login.")
        st.stop()


def hide():
    hide_pages([
        'confirm', 'user_home', 'add_book', 'account_settings', 'confirm_entry',
        'add_character', 'add_author', 'book_data_entry', 'enter_text',
        'register_user', 'register_user_done'
    ])


def check_user_exists(username):
    db = FirestoreWrapper().connect(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    return len(docs) >= 1


def authenticate_user(username, password):
    db = FirestoreWrapper().connect(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    if len(docs) == 1:
        if not docs[0].to_dict()['is_confirmed']:
            return False

        stored_password = docs[0].to_dict()['password']
        return bcrypt.checkpw(
            password=password.encode('utf8'),
            hashed_password=stored_password.encode('utf8')
        )

    return False


def hash_password(password):
    hashed_password = bcrypt.hashpw(
        password.encode('utf8'), bcrypt.gensalt()
    ).decode('utf8')
    return hashed_password


def send_confirmation_email(send_to, username, confirmation_token, name):

    smtpserver = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    smtpserver.ehlo()
    smtpserver.login(st.secrets["email_address"], st.secrets["gmail_app_password"])

    subject = "Please confirm your account registration"
    body = """
        Dear %s, 
        
        Thank you for registering for an account for our data entry tool.
        Please click the link below to confirm your registration.
        
        If you did not register, please reply to this email to let us know
        and we will delete your email address.
        
        Thanks,
        The Fair Tales team
        
    """ % name
    confirmation_link = f"{st.secrets['app_url']}confirm?token={confirmation_token}&user={username}"
    body += confirmation_link
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = st.secrets["email_address"]
    msg['To'] = send_to

    smtpserver.send_message(msg)
    smtpserver.close()


def author_entry_to_name(entry):
    """
    Helper method converts an author entry from the Firestore database
    to a readable string as 'forename surname'.
    """
    author = entry.to_dict()
    return ' '.join([author['forename'], author['surname']])


class FirestoreWrapper:
    """
    Wrapper class to handle interacting with
    Firestore database (searching, querying, entering new data).
    """

    def __init__(self):
        self.firestore_key = json.loads(st.secrets["firestore_key"])

    def connect(self, auth=True):
        if is_authenticated() or not auth:
            creds = service_account.Credentials.from_service_account_info(self.firestore_key)
            return firestore.Client(credentials=creds, project="sawdataentry")
        else:
            return None

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
