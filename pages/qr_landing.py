import streamlit as st
from utilities import page_layout, FirestoreWrapper, check_authentication_status
from pages.uploader import upload_widget
from Home import ensure_session
from data_structures import Book
from text_content import Instructions, Alerts, QrLanding

check_authentication_status()
page_layout()

token = st.query_params.token
user = st.query_params.user
book = st.query_params.book

st.title(QrLanding.title)
st.write(Instructions.photo_upload_instructions)
st.write(Instructions.photo_naming_instructions)

db_user = FirestoreWrapper().connect_user(auth=False)
db_book = FirestoreWrapper().connect_book(auth=False)
user_ref = db_user.collection("users").document(user)

if token == user_ref.get().to_dict()['confirmation_token']:
    # Home.py's ensure_session() has already populated firestore + lookup dicts
    # before this page ran; this call is an idempotent no-op kept for the case
    # where the page is reached without that guarantee. current_book is set
    # explicitly from the QR's book param below regardless.
    ensure_session()
    st.session_state['authentication_status'] = True
    st.session_state['username'] = user
    st.session_state['current_book'] = Book(
        db_book.collection('books').document(book).get().to_dict()
    )
    upload_widget(on_submit='close')

else:
    st.warning(Alerts.invalid_credentials)
    st.write(user)
    st.write(token)
    st.write(book)
