import streamlit as st
from utilities import page_layout
from text_content import Alerts, RegisterUser

page_layout()


with st.form('registration_form'):
    st.title(RegisterUser.title)
    st.success(Alerts.email_sent)
    registered = st.form_submit_button(RegisterUser.register_button, disabled=True, key="register_done_button")
