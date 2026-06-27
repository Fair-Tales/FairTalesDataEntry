from google.cloud import firestore
import streamlit as st
from google.cloud.firestore_v1 import FieldFilter
from google.oauth2 import service_account
import pandas as pd
import json
import re
import urllib.request
import bcrypt
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone

def is_authenticated():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    return st.session_state['authentication_status']


def check_authentication_status():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    if not is_authenticated():
        st.switch_page("./pages/login.py")


_MAX_HISTORY = 10


def navigate_to(page_path):
    """Navigate to a page, pushing the current page onto the back-history stack."""
    current = st.session_state.get('_current_page', None)
    if current:
        history = st.session_state.get('_page_history', [])
        history.append(current)
        st.session_state['_page_history'] = history[-_MAX_HISTORY:]
    st.switch_page(page_path)


def go_back(fallback="./pages/user_home.py"):
    """Navigate to the previous page in the history stack."""
    history = st.session_state.get('_page_history', [])
    if history:
        previous = history.pop()
        st.session_state['_page_history'] = history
        st.switch_page(previous)
    else:
        st.switch_page(fallback)


def clear_page_history():
    """Reset the back-history stack (used at root pages and on logout)."""
    st.session_state['_page_history'] = []


def page_layout(current_page=None):
    st.set_page_config(
        initial_sidebar_state="collapsed",
        layout="wide"
    )
    if current_page:
        st.session_state['_current_page'] = current_page
    st.sidebar.page_link("pages/login.py", label="Login")
    st.sidebar.page_link("pages/user_home.py", label="Home")
    st.sidebar.page_link("pages/account_settings.py", label="Settings")
    if 'admin' in st.session_state and st.session_state['admin']:
        st.sidebar.page_link("pages/admin.py", label="Admin")
    history = st.session_state.get('_page_history', [])
    # Hide Back during the guided book sub-entry flow (add author/illustrator/
    # publisher): returning to add_book.py would just re-forward here. Use Cancel.
    if history and not st.session_state.get('adding_book_entries', False):
        if st.sidebar.button("← Back"):
            go_back()



def get_user(username):
    db = FirestoreWrapper().connect_user(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    if len(docs) == 1:
        return docs[0]
    else:
        return None
    
def get_admin(username):
    user = get_user(username)
    return user.to_dict().get('admin', False)


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


def extract_isbn(text):
    """Extract ISBN-13 or ISBN-10 from text. Returns string or None.

    Real-world copyright pages hyphenate ISBNs with varied group sizes, so we
    match a run of digits separated by optional hyphens/spaces and validate the
    cleaned length rather than assuming a fixed grouping.
    """
    if not text:
        return None
    isbn13 = re.search(r'97[89][-\s]?(?:\d[-\s]?){9}\d', text)
    if isbn13:
        return re.sub(r'[-\s]', '', isbn13.group())
    isbn10 = re.search(r'\b\d[-\s]?(?:\d[-\s]?){8}[\dX]\b', text)
    if isbn10:
        return re.sub(r'[-\s]', '', isbn10.group())
    return None


def lookup_isbn(isbn):
    """
    Look up book metadata via the Google Books API (free, no auth required).
    Returns dict with keys title, authors, publisher, published_date,
    or None on any failure.
    """
    if not isbn:
        return None
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&maxResults=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get('totalItems', 0) == 0:
            return None
        info = data['items'][0]['volumeInfo']
        return {
            'title': info.get('title', ''),
            'authors': info.get('authors', []),
            'publisher': info.get('publisher', ''),
            'published_date': info.get('publishedDate', ''),
        }
    except Exception:
        return None


class FirestoreWrapper:
    """
    Wrapper class to handle interacting with
    Firestore database (searching, querying, entering new data).
    """

    def __init__(self, auth=True):
        self.auth = auth
        self.firestore_key = json.loads(st.secrets["firestore_key"])

    def _connect(self, auth=None):
        auth = self.auth if auth is None else auth
        if is_authenticated() or not auth:
            creds = service_account.Credentials.from_service_account_info(self.firestore_key)
            return firestore.Client(credentials=creds, project="sawdataentry")
        else:
            return None

    # connect_book and connect_user are kept as separate methods in anticipation
    # of issue #48, which will split the single Firestore database into two:
    # one for book/content data and one for user credentials. When that work is
    # done, each method will connect to its own named database. For now both
    # route to the same default database.
    def connect_book(self, auth=None):
        return self._connect(auth)

    def connect_user(self, auth=None):
        return self._connect(auth)

    def single_field_search(self, collection, field, contains_string):
        """ Search for string withing field. """
        db = self.connect_book()

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
        db = self.connect_book()
        results = db.collection(collection).where(
            filter=FieldFilter(field, "==", match)
        ).stream()
        # return doc_ref.get()

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_reference(self, collection, document_ref):
        db = self.connect_book()
        doc_ref = db.collection(collection).document(document_ref)
        return doc_ref.get()

    def get_all_documents_stream(self, collection):
        db = self.connect_book()
        return db.collection(collection).stream()

    def username_to_doc_ref(self, username):
        return self.connect_user().collection('users').document(username)

    def document_exists(self, collection, doc_id):
        db = self.connect_book()
        doc = db.collection(collection).document(doc_id).get()
        return doc.exists

    def update_field(self, collection, document, field, value):
        db = self.connect_book()
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
        'new_illustrator': 'confirm_new_illustrator',
        'new_publisher': 'confirm_new_publisher',
        'new_character': 'confirm_new_character'
    }

    @classmethod
    def display_confirmation(cls, data):

        # Compact, borderless key/value summary in a constrained-width column,
        # rather than a full-width bordered table.
        summary_col, _ = st.columns([2, 1])
        for field, value in data.items():
            label = field.replace('_', ' ').capitalize()
            display_value = "" if value is None else value
            summary_col.markdown(f"**{label}:** {display_value}")
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
                navigate_to("./pages/add_author.py")

            else:
                st.session_state['current_book'].register()
                st.session_state['book_dict'][
                    st.session_state['current_book'].title
                ] = st.session_state['current_book'].get_ref()
                st.session_state.pop('isbn_metadata', None)

                if st.session_state.current_book.photos_uploaded:
                    navigate_to("./pages/enter_text.py")
                else:
                    navigate_to("./pages/page_photo_upload.py")

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
            st.session_state['author_dict'][
                st.session_state['current_author'].name
            ] = st.session_state['current_author'].get_ref()

            st.session_state['current_book'].author = (
                st.session_state['current_author'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_author.py")

    @classmethod
    def confirm_new_illustrator(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_illustrator'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_illustrator'].register()
            st.session_state['illustrator_dict'][
                st.session_state['current_illustrator'].name
            ] = st.session_state['current_illustrator'].get_ref()

            st.session_state['current_book'].illustrator = (
                st.session_state['current_illustrator'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_illustrator.py")

    @classmethod
    def confirm_new_publisher(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_publisher'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_publisher'].register()
            st.session_state['publisher_dict'][
                st.session_state['current_publisher'].name
            ] = st.session_state['current_publisher'].get_ref()

            st.session_state['current_book'].publisher = (
                st.session_state['current_publisher'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_publisher.py")

    @classmethod
    def confirm_new_character(cls):
        confirm_button, edit_button = cls.display_confirmation('character_details')

        if confirm_button:
            navigate_to("./pages/book_data_entry.py")

        if edit_button:
            st.switch_page("./pages/add_character.py")


@st.dialog("Are you sure?")
def confirm_submit():
    st.write(
        """
        Are you sure you want to submit this book? You will not be able to edit it again after submission,
        so please only submit once you are confident that everything is correct and complete.
        """
    )
    if st.button("Confirm"):
        st.session_state.current_book.entry_status = 'completed'
        st.session_state.current_book.datetime_submitted = datetime.now(timezone.utc)
        clear_page_history()
        st.switch_page("./pages/user_home.py")
    if st.button("Cancel"):
        st.rerun()
