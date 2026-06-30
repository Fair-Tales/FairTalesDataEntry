import streamlit as st
import pandas as pd
from utilities import page_layout, check_authentication_status, databot_entered_by
from text_content import Instructions, Alerts, ReviewBooks
from data_structures import Book

check_authentication_status()
page_layout()

# Editing is OWN-BOOKS-ONLY for ALL roles (#131, reversing part of #83): every
# user — archivist, team member or admin — may edit only the books they entered
# themselves, PLUS books owned by the special ``databot`` system user (the owner
# of AI-generated books). databot books are deliberately editable by anyone so an
# AI-reconstructed book can be picked up and finished/corrected by whoever is free.
firestore = st.session_state.firestore
user_ref = firestore.username_to_doc_ref(st.session_state['username'])
databot_ref = databot_entered_by()

# Own books: only those still in progress can be edited; submitted ('completed')
# books are locked after submission.
own_books = firestore.get_by_field(collection="books", field="entered_by", match=user_ref)
if len(own_books):
    own_books = own_books.loc[own_books.entry_status == 'started']

# databot (AI-generated) books: shown REGARDLESS of entry_status so anyone can
# pick one up. STATUS CHOICE — flagged for Chris to confirm: databot books are
# offered on the edit page even when entry_status == 'completed' (i.e. already in
# the validation queue), unlike a user's own books which are limited to 'started'.
databot_books = firestore.get_by_field(collection="books", field="entered_by", match=databot_ref)

# Combine, guarding empties (an empty get_by_field DataFrame has no columns, so it
# cannot be filtered/concatenated meaningfully). drop_duplicates on title is
# defensive — a book has a single owner, so the two sets never actually overlap.
frames = [df for df in (own_books, databot_books) if len(df)]
if frames:
    my_books = pd.concat(frames, ignore_index=True).drop_duplicates(subset="title")
else:
    my_books = pd.DataFrame()

if len(my_books) == 0:
    st.warning(Alerts.no_user_books)
else:
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
