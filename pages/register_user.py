import streamlit as st
from datetime import datetime
import bcrypt
st.set_page_config(
    initial_sidebar_state="auto"
)
from utilities import (
    hide, hash_password, check_user_exists,
    send_confirmation_email, FirestoreWrapper
)
from text_content import Alerts, GenderRegistration

hide()


def register_user(_username, _password, _gender, _birth_year):
    hashed_password = hash_password(_password)
    confirmation_token = bcrypt.gensalt().decode('utf8')
    now = datetime.now()
    user_data = {
        "username": _username,
        "password": hashed_password,
        "account_creation_date": now,
        "last_active": now,
        "user_gender": _gender,
        "user_birth_year": _birth_year,
        "account_type": "user",
        "confirmation_token": confirmation_token,
        "is_confirmed": False
    }
    if check_user_exists(_username):
        st.warning(Alerts.user_exists)
    else:
        db = FirestoreWrapper().connect(auth=False)
        db.collection("users").document(username).set(user_data)
        send_confirmation_email(username, username, confirmation_token)
        st.switch_page("./pages/register_user_done.py")


def validate_user_details(_username, _password, _gender, _user_birth_year):

    fields = {
        "Email": _username,
        "Password": _password,
        "Gender": _gender,
        "Birth year": _user_birth_year
    }

    for field in fields.keys():
        if fields[field] == "":
            st.warning(Alerts.no_blank_field(field))
            return False

    return True


with st.form('registration_form'):
    st.title("User Registration")
    username = st.text_input("Email", value="", key='register_email')
    password = st.text_input("Password", type="password", value="", key='register_password')
    user_gender = st.selectbox(
        GenderRegistration.question,
        GenderRegistration.options
    )
    if user_gender == GenderRegistration.manual_input_option:
        print("SELL")
        gender = st.text_input(label=GenderRegistration.manual_input_prompt, value="")
    else:
        gender = user_gender

    user_birth_year = st.number_input("Birth year", min_value=1900, max_value=2030, value=1980)
    registered = st.form_submit_button("Register")

    if registered:
        if validate_user_details(username, password, gender, user_birth_year):
            register_user(username, password, gender, user_birth_year)
