import streamlit as st
from utilities import author_entry_to_name, hide

hide()


with st.form("new_author"):
    st.session_state['current_author'].to_form()


cancel_button = st.button("Cancel entering new author.")

if cancel_button:
    st.switch_page("./pages/add_book.py")

