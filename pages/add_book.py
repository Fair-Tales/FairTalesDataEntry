import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import BookForm, BookPhotoEntry

check_authentication_status()
page_layout(current_page="./pages/add_book.py")

metadata = st.session_state.get('book_extraction') or {}
if (metadata.get('isbn_lookup') or {}).get('status') == 'failed':
    st.warning(BookPhotoEntry.isbn_lookup_failed)

with st.form("new_book"):
    st.session_state['current_book'].to_form()


cancel_button = st.button(BookForm.cancel_text, help=BookForm.cancel_help, key="add_book_cancel_button")

if cancel_button:
    st.switch_page("./pages/user_home.py")
