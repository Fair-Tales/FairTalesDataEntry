import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import PublisherForm

check_authentication_status()
page_layout(current_page="./pages/add_publisher.py")


with st.form("new_publisher"):
    st.session_state['current_publisher'].to_form()


cancel_button = st.button(PublisherForm.cancel_text, key="add_publisher_cancel_button")

if cancel_button:
    st.session_state['adding_book_entries'] = False
    st.switch_page("./pages/add_book.py")

