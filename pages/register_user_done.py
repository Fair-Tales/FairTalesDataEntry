import streamlit as st
from utilities import hide
from text_content import Alerts

hide()


with st.form('registration_form'):
    st.title("User Registration")
    st.success(Alerts.email_sent)
    registered = st.form_submit_button("Register", disabled=True)
