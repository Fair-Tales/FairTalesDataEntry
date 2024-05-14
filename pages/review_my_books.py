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
selected_title = st.selectbox(
    label="My entered books:",
    options=my_books.title
)

selected_book = Book(
    my_books[my_books.title == selected_title].iloc[0]
)
st.write(
    selected_book.to_dict(convert_ref_fields_to_ids=True, form_fields_only=True)
)

edit_button = st.button("Edit this book.")
cancel_button = st.button("Cancel editing books.")

if edit_button:
    st.session_state['current_book'] = selected_book
    st.switch_page("./pages/add_book.py")

if cancel_button:
    st.switch_page("./pages/user_home.py")
