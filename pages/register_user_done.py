import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import Alerts

page_layout()


with st.form('registration_form'):
    st.title("User Registration")
    st.success(Alerts.email_sent)
    registered = st.form_submit_button("Register", disabled=True)
