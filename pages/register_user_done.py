import streamlit as st
from datetime import datetime
import bcrypt
from utilities import (
    hide, hash_password, check_user_exists,
    send_confirmation_email, FirestoreWrapper
)
from text_content import Alerts, GenderRegistration

hide()


with st.form('registration_form'):
    st.title("User Registration")
    st.success(Alerts.email_sent)
    registered = st.form_submit_button("Register", disabled=True)
