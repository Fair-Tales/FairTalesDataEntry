import streamlit as st
from datetime import datetime
import bcrypt
from utilities import (
    hide, hash_password, check_user_exists,
    send_confirmation_email, FirestoreWrapper
)

hide()


def register_user(username, password, user_gender, user_birth_year):
    hashed_password = hash_password(password)
    confirmation_token = bcrypt.gensalt().decode('utf8')
    now = datetime.now()
    user_data = {
        "username": username,
        "password": hashed_password,
        "account_creation_date": now,
        "last_active": now,
        "user_gender": user_gender,
        "user_birth_year": user_birth_year,
        "account_type": "user",
        "confirmation_token": confirmation_token,
        "is_confirmed": False
    }
    if check_user_exists(username):
        st.warning("Username already in use! Please choose another.")
    else:
        db = FirestoreWrapper().connect(auth=False)
        db.collection("users").document(username).set(user_data)
        send_confirmation_email(username, username, confirmation_token)
        st.success("You have been sent an email - please click the link in the message to continue registration.")


st.title("User Registration")
username = st.text_input("Email")
password = st.text_input("Password", type="password")
user_gender = st.selectbox("Gender", ["Female", "Male", "Other"])
user_birth_year = st.number_input("Birth year", min_value=1900, max_value=2030, value=1980)

if st.button("Register"):
    register_user(username, password, user_gender, user_birth_year)
