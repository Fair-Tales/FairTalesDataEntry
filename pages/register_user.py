import streamlit as st
from datetime import datetime, date
import bcrypt
from utilities import (
    page_layout, hash_password, check_user_exists, send_confirmation_email, FirestoreWrapper
)
from text_content import Alerts, GenderRegistration


def register_user(_username, _name, _password, _gender, _birth_year):
    hashed_password = hash_password(_password)
    confirmation_token = bcrypt.gensalt().decode('utf8')
    now = datetime.now()
    user_data = {
        "username": _username,
        "name": _name,
        "password": hashed_password,
        "account_creation_date": now,
        "last_active": now,
        "user_gender": _gender,
        "user_birth_year": _birth_year,
        "account_type": "user",
        "confirmation_token": confirmation_token,
        "is_confirmed": False,
        "trust_rating": 0,
        "admin": False
    }
    if check_user_exists(_username):
        st.warning(Alerts.user_exists)
    else:
        db = FirestoreWrapper().connect_user(auth=False)
        db.collection("users").document(username).set(user_data)
        send_confirmation_email(username, username, confirmation_token, _name)
        st.switch_page("./pages/register_user_done.py")


def is_valid_email(email):
    return ('@' in email) and ('.' in email)


def validate_user_details(_username, _name, _password, _gender, _gender_custom, _user_birth_year):

    fields = {
        "Email": _username,
        "Name": _name,
        "Password": _password,
        "Birth year": _user_birth_year
    }

    for field in fields.keys():
        if fields[field] == "":
            st.warning(Alerts.no_blank_field(field))
            return False

    if not is_valid_email(_username):
        st.warning(Alerts.invalid_email)
        return False

    if _gender is None or _gender == "":
        if _gender_custom == "":
            st.warning(Alerts.please_enter_gender)
            return False
    else:
        if _gender_custom != "" and _gender not in (None, "", GenderRegistration.manual_input_option):
            st.warning(Alerts.please_select_other)
            return False

    return True


page_layout()

with st.form('registration_form'):
    st.title("User Registration")
    username = st.text_input("Email", value="", key='register_email').lower()
    name = st.text_input("Name", value="", key='name_of_user')
    password = st.text_input("Password", type="password", value="", key='register_password')
    gender = st.selectbox(
        GenderRegistration.question,
        GenderRegistration.options,
        index=None,
        help=GenderRegistration.help
    )
    gender_custom = st.text_input(label=GenderRegistration.manual_input_prompt, value="")

    user_birth_year = int(st.selectbox(
        "What is your birth year?",
        (x for x in range(1900, (date.today().year + 1))),
        placeholder="Select year of birth",
        ))
    registered = st.form_submit_button("Register")

    if registered:
        if validate_user_details(username, name, password, gender, gender_custom, user_birth_year):
            register_user(username, name, password, gender, user_birth_year)
