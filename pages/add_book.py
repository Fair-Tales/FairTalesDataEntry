import streamlit as st
from utilities import hide
from text_content import BookForm

hide()

with st.form("new_book"):
    st.session_state['current_book'].to_form()


cancel_button = st.button(BookForm.cancel_text, help=BookForm.cancel_help)

if cancel_button:
    st.switch_page("./pages/user_home.py")
