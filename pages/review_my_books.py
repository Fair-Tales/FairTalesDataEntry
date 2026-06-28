import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import Instructions, Alerts, ReviewBooks
from data_structures import Book

check_authentication_status()
page_layout()

user_ref = st.session_state.firestore.username_to_doc_ref(st.session_state['username'])
my_books = st.session_state.firestore.get_by_field(
    collection="books",
    field="entered_by",
    match=user_ref
)

if len(my_books) == 0:
    st.warning(Alerts.no_user_books)
else:
    my_books = my_books.loc[my_books.entry_status == 'started']
    st.header(ReviewBooks.header)
    st.write(Instructions.review_my_books)
    selected_title = st.selectbox(
        label=ReviewBooks.select_label,
        options=my_books.title,
        key="review_books_select"
    )

    selected_book = Book(
        my_books[my_books.title == selected_title].iloc[0]
    )

    edit_button = st.button(ReviewBooks.edit_button, key="review_books_edit_button")
    if edit_button:
        st.session_state['current_book'] = selected_book
        st.session_state['current_book'].editing = True
        st.switch_page("./pages/book_edit_home.py")

cancel_button = st.button(ReviewBooks.cancel_button, key="review_books_cancel_button")

if cancel_button:
    st.switch_page("./pages/user_home.py")
