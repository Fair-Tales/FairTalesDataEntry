import streamlit as st
from utilities import page_layout, check_authentication_status

check_authentication_status()
page_layout()


with st.form("new_illustrator"):
    st.session_state['current_illustrator'].to_form()


cancel_button = st.button("Cancel entering new illustrator.")

if cancel_button:
    st.session_state['adding_book_entries'] = False
    st.switch_page("./pages/add_book.py")

