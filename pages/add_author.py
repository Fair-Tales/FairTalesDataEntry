import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import AuthorForm

check_authentication_status()
page_layout(current_page="./pages/add_author.py")


with st.form("new_author"):
    st.session_state['current_author'].to_form()


cancel_button = st.button(AuthorForm.cancel_text, key="add_author_cancel_button")

if cancel_button:
    st.session_state['adding_book_entries'] = False
    st.switch_page("./pages/add_book.py")

