import streamlit as st
st.set_page_config(
    page_title="bde",
    initial_sidebar_state="collapsed"
)
from utilities import hide
from text_content import Instructions
from data_structures import Book

hide()

db = st.session_state.firestore.connect()
user_ref = db.document(
    f"users/{st.session_state['username']}"
)


my_books = st.session_state.firestore.get_by_field(
    collection="books",
    field="entered_by",
    match=user_ref
)
my_books = my_books.loc[my_books.entry_status=='started']
st.header("Review my books")
st.write(Instructions.review_my_books)
selected_title = st.selectbox(
    label="My entered books:",
    options=my_books.title
)

selected_book = Book(
    my_books[my_books.title == selected_title].iloc[0]
)

edit_button = st.button("Edit this book.")
cancel_button = st.button("Cancel editing books.")

if edit_button:
    st.session_state['current_book'] = selected_book
    st.session_state['current_book'].editing = True
    st.switch_page("./pages/book_edit_home.py")

if cancel_button:
    st.switch_page("./pages/user_home.py")
