import streamlit as st
from datetime import datetime
import bcrypt
from utilities import (
    hide, hash_password, check_user_exists,
    send_confirmation_email, FirestoreWrapper
)
from text_content import Alerts

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
        st.success(Alerts.email_sent)


st.title("User Registration")
username = st.text_input("Email", value="", key='register_email')
password = st.text_input("Password", type="password", value="", key='register_password')
user_gender = st.selectbox(
    "Which most accurately describes your gender identity?",
    ["Woman", "Man", "Non-binary", "Let me type...", "Prefer not to say"]
)
if user_gender == "Let me type...":
    gender = st.text_input(label="Please describe your gender identify.", value="")
else:
    gender = user_gender

user_birth_year = st.number_input("Birth year", min_value=1900, max_value=2030, value=1980)

if st.button("Register"):
    register_user(username, password, gender, user_birth_year)
