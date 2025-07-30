import streamlit as st
from utilities import page_layout, check_authentication_status

check_authentication_status()
page_layout()


with st.form("new_author"):
    st.session_state['current_author'].to_form()


cancel_button = st.button("Cancel entering new author.")

if cancel_button:
    st.session_state['adding_book_entries'] = False
    st.switch_page("./pages/add_book.py")

