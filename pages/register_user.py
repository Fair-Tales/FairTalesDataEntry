import streamlit as st
from datetime import datetime, date
import bcrypt
import email.utils
from utilities import (
    page_layout, hash_password, send_confirmation_email, FirestoreWrapper,
    ROLE_ARCHIVIST,
)
from text_content import Alerts, GenderRegistration, RegisterUser


def register_user(_username, _name, _password, _gender, _birth_year, _newsletter_opt_in=False):
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
        "admin": False,
        # New users default to the archivist tier (#83). 'admin' is kept for
        # back-compat; an admin promotes a user by setting 'role' on the doc.
        "role": ROLE_ARCHIVIST,
        "newsletter_opt_in": _newsletter_opt_in
    }
    # Use the general FirestoreWrapper.document_exists() helper for the
    # existence check (issue #53) rather than an inline get().exists. auth=False
    # because the user is not yet authenticated during registration.
    firestore_wrapper = FirestoreWrapper(auth=False)
    if firestore_wrapper.document_exists(collection='users', doc_id=_username):
        st.warning(Alerts.user_exists)
    else:
        db = firestore_wrapper.connect_user(auth=False)
        db.collection("users").document(_username).set(user_data)
        send_confirmation_email(_username, _username, confirmation_token, _name)
        st.switch_page("./pages/register_user_done.py")


def is_valid_email(address):
    """Basic RFC 5322 email validation using the standard library."""
    if not address or len(address) > 254:
        return False
    try:
        parsed = email.utils.parseaddr(address)
        if not parsed[1] or '@' not in parsed[1]:
            return False
        local, domain = parsed[1].rsplit('@', 1)
        return bool(local) and bool(domain) and '.' in domain
    except Exception:
        return False


def validate_user_details(_username, _name, _password, _gender, _gender_custom, _user_birth_year):

    fields = {
        RegisterUser.email_label: _username,
        RegisterUser.name_label: _name,
        RegisterUser.password_label: _password,
        RegisterUser.birth_year_field: _user_birth_year
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
    st.title(RegisterUser.title)
    username = st.text_input(RegisterUser.email_label, value="", key='register_email').lower().strip()
    name = st.text_input(RegisterUser.name_label, value="", key='name_of_user').strip()
    password = st.text_input(RegisterUser.password_label, type="password", value="", key='register_password')
    gender = st.selectbox(
        GenderRegistration.question,
        GenderRegistration.options,
        index=None,
        help=GenderRegistration.help,
        key="register_gender_select"
    )
    gender_custom = st.text_input(label=GenderRegistration.manual_input_prompt, value="", key="register_gender_custom_input")

    user_birth_year = int(st.selectbox(
        RegisterUser.birth_year_label,
        (x for x in range(1900, (date.today().year + 1))),
        placeholder=RegisterUser.birth_year_placeholder,
        key="register_birth_year_select"
        ))
    newsletter_opt_in = st.checkbox(
        RegisterUser.newsletter_label,
        value=False,
        key="register_newsletter_checkbox"
    )
    registered = st.form_submit_button(RegisterUser.register_button, key="register_submit_button")

    if registered:
        if validate_user_details(username, name, password, gender, gender_custom, user_birth_year):
            register_user(username, name, password, gender, user_birth_year, newsletter_opt_in)
