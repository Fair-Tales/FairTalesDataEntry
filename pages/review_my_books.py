import streamlit as st
from utilities import get_user, hide, FirestoreWrapper
from text_content import Alerts, Instructions
from data_structures import Book

hide()

db = FirestoreWrapper().connect()
user_ref = db.document(
    f"users/{st.session_state['username']}"
)


my_books = st.session_state.firestore.get_by_field(
    collection="books",
    field="entered_by",
    match=user_ref
)
st.header("Review my books")
st.write(Instructions.review_my_books)
st.selectbox(
    label="My entered books:",
    options=my_books.title
)

st.write(Book(my_books.iloc[0]).to_dict())

edit_button = st.button("Edit this book.")
cancel_button = st.button("Cancel editing books.")

if edit_button:
    # TODO: load book from db into session state
    st.warning(Alerts.not_implemented)

if cancel_button:
    st.switch_page("./pages/user_home.py")
