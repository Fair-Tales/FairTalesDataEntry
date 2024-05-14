import streamlit as st
from utilities import hide

hide()

with st.form("new_book"):
    st.session_state['current_book'].to_form()


cancel_button = st.button("Cancel entering new book.")

if cancel_button:
    st.switch_page("./pages/user_home.py")
