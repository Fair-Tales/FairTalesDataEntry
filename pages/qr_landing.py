import streamlit as st
from utilities import page_layout, FirestoreWrapper, check_authentication_status
from pages.uploader import upload_widget
from Home import initialise
from data_structures import Book
from text_content import Instructions

check_authentication_status()
page_layout()

token = st.query_params.token
user = st.query_params.user
book = st.query_params.book

st.title("Photo uploader.")
st.write(Instructions.photo_upload_instructions)

db_user = FirestoreWrapper().connect_user(auth=False)
db_book = FirestoreWrapper().connect_book(auth=False)
user_ref = db_user.collection("users").document(user)

if token == user_ref.get().to_dict()['confirmation_token']:
    initialise()
    st.session_state['authentication_status'] = True
    st.session_state['username'] = user
    st.session_state['current_book'] = Book(
        db_book.collection('books').document(book).get().to_dict()
    )
    upload_widget(on_submit='close')

else:
    st.warning("Invalid credentials.")
    st.write(user)
    st.write(token)
    st.write(book)
