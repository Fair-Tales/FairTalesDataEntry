import streamlit as st
from utilities import page_layout, check_authentication_status

check_authentication_status()
page_layout()


with st.form("new_publisher"):
    st.session_state['current_publisher'].to_form()


cancel_button = st.button("Cancel entering new publisher.")

if cancel_button:
    st.switch_page("./pages/add_book.py")

