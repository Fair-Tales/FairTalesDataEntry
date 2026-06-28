import streamlit as st
import pandas as pd
from utilities import page_layout, check_authentication_status, is_team_or_above
from text_content import Instructions, Alerts, ReviewBooks
from data_structures import Book

check_authentication_status()
page_layout()

# Archivists may only edit books they uploaded themselves (entered_by == them).
# Team members and admins may also edit books uploaded by others (#83), so they
# load every book rather than just their own.
if is_team_or_above():
    docs = st.session_state.firestore.get_all_documents_stream(collection="books")
    my_books = pd.DataFrame([doc.to_dict() for doc in docs])
    header = ReviewBooks.all_header
    select_label = ReviewBooks.all_select_label
else:
    user_ref = st.session_state.firestore.username_to_doc_ref(st.session_state['username'])
    my_books = st.session_state.firestore.get_by_field(
        collection="books",
        field="entered_by",
        match=user_ref
    )
    header = ReviewBooks.header
    select_label = ReviewBooks.select_label

if len(my_books) == 0:
    st.warning(Alerts.no_user_books)
else:
    # Only books still in progress can be edited; submitted ('completed') books
    # are locked after submission.
    my_books = my_books.loc[my_books.entry_status == 'started']
    st.header(header)
    st.write(Instructions.review_my_books)
    selected_title = st.selectbox(
        label=select_label,
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
