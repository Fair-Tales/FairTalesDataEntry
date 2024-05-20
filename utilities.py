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
        'register_user', 'register_user_done', 'review_my_books',
        'page_photo_upload'
    ])


def check_user_exists(username):
    db = FirestoreWrapper().connect(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    return len(docs) >= 1


def get_user(username):
    db = FirestoreWrapper().connect(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    if len(docs) == 1:
        return docs[0]
    else:
        return None


def authenticate_user(username, password):

    user = get_user(username)
    if user is not None:
        if not user.to_dict()['is_confirmed']:
            return False

        stored_password = user.to_dict()['password']
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
        
        Thank you for registering for an account on our data entry tool.
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

    def __init__(self, auth=True):
        self.auth = auth
        self.firestore_key = json.loads(st.secrets["firestore_key"])

    def connect(self, auth=None):

        auth = self.auth if auth is None else auth
        if is_authenticated() or not auth:
            creds = service_account.Credentials.from_service_account_info(self.firestore_key)
            return firestore.Client(credentials=creds, project="sawdataentry")
        else:
            return None

    def single_field_search(self, collection, field, contains_string):
        """ Search for string withing field. """
        db = self.connect()

        results = (
            db.collection(collection)
                .where(filter=FieldFilter(field, ">=", contains_string))
                .where(filter=FieldFilter(field, "<=", contains_string + 'z'))
                .stream()
        )

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_field(self, collection, field, match):
        """ Get exact match in field"""
        db = self.connect()
        results = db.collection(collection).where(
            filter=FieldFilter(field, "==", match)
        ).stream()
        # return doc_ref.get()

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_reference(self, collection, document_ref):
        db = self.connect()
        doc_ref = db.collection(collection).document(document_ref)
        return doc_ref.get()

    def get_all_documents_stream(self, collection):
        db = self.connect()
        return db.collection(collection).stream()

    def username_to_doc_ref(self, username):
        return self.connect().collection('users').document(username)

    def document_exists(self, collection, doc_id):
        db = self.connect()
        doc = db.collection(collection).document(doc_id).get()
        return doc.exists

    def update_field(self, collection, document, field, value):
        db = self.connect()
        doc_ref = db.collection(collection).document(document)
        doc_ref.update({field: value})


# TODO: check that required fields (e.g. book title) are not blank
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
    def display_confirmation(cls, data):

        st.dataframe(data, use_container_width=True)
        col1, col2 = st.columns(2)
        confirm_button = col1.button("Confirm")
        edit_button = col2.button("Edit")

        return confirm_button, edit_button

    @classmethod
    def confirm_new_book(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_book'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            if st.session_state['current_book'].author == "None of these (create a new author).":
                st.switch_page("./pages/add_author.py")

            else:
                # TODO: only save if Book has been modified
                st.session_state['current_book'].register()
                st.session_state['current_book'].save_to_db()
                st.session_state['book_dict'][
                    st.session_state['current_book'].title
                ] = st.session_state['current_book'].get_ref()

                if st.session_state.current_book.photos_uploaded:
                    st.switch_page("./pages/enter_text.py")
                else:
                    st.switch_page("./pages/page_photo_upload.py")

        if edit_button:
            st.switch_page("./pages/add_book.py")

    @classmethod
    def confirm_new_author(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_author'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_author'].register()
            st.session_state['current_author'].save_to_db()
            st.session_state['author_dict'][
                st.session_state['current_author'].name
            ] = st.session_state['current_author'].get_ref()

            st.session_state['current_book'].set_author(
                st.session_state['current_author'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_author.py")

    @classmethod
    def confirm_new_character(cls):
        confirm_button, edit_button = cls.display_confirmation('character_details')

        if confirm_button:
            st.switch_page("./pages/book_data_entry.py")

        if edit_button:
            st.switch_page("./pages/add_character.py")
