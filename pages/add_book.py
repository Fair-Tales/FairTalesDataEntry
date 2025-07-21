import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import BookForm

check_authentication_status()
page_layout()

with st.form("new_book"):
    st.session_state['current_book'].to_form()


cancel_button = st.button(BookForm.cancel_text, help=BookForm.cancel_help)

if cancel_button:
    st.switch_page("./pages/user_home.py")
