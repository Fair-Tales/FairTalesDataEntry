import streamlit as st
from email.mime.text import MIMEText
from google.cloud import firestore
from datetime import datetime
import bcrypt
import smtplib
st.set_page_config(
    page_title="Home",
    initial_sidebar_state="expanded"
)
from utilities import hide, is_authenticated, FirestoreWrapper

# TODO: migrate to json token to secrets.toml (https://discuss.streamlit.io/t/how-to-use-an-entire-json-file-in-the-secrets-app-settings-when-deploying-on-the-community-cloud/49375/2)

# TODO:make better use of st-pages to show/hide and use icons: https://github.com/blackary/st_pages?tab=readme-ov-file

# TODO: remove credentials wrapper utility?
# TODO: add wrapper for firestore database (e.g. to search authors etc)
# TODO: refactor some login/registration code from here
# TODO: add different redirect to admin_home is user is admin

# TODO: add options menu; https://discuss.streamlit.io/t/streamlit-option-menu-is-a-simple-streamlit-component-that-allows-users-to-select-a-single-item-from-a-list-of-options-in-a-menu/20514
# TODO: add TCs at registration.


# TODO: ensure username/email unique
# TODO: what genders when registering?

# TODO: Add password retrieval and reset (and allow other user info to be changed?)
# TODO: make fields blank when switching register/login and vice verca
# TODO: schedule user database backup?
# TODO: currently just using username as email. Happy with this?

# TODO: add confirmation check before some actions (e..g. cancel book entry): https://www.aprime.io/streamlit-tutorial-dynamic-confirmation-modals-session-state/

db = firestore.Client.from_service_account_json("./secrets/firestore_service_account_key.json")
users_ref = db.collection("users")


def login():
    st.title("Login")
    username = st.text_input("Email", value="")
    password = st.text_input("Password", type="password", value="")
    if st.button("Login"):
        if authenticate_user(username, password):
            st.session_state['authentication_status'] = True
            st.switch_page("./pages/user_home.py")
        else:
            st.error("Invalid credentials.")


def register():

    st.title("User Registration")
    username = st.text_input("Email")
    password = st.text_input("Password", type="password")
    user_gender = st.selectbox("Gender", ["Female", "Male", "Other"])
    user_birth_year = st.number_input("Birth year", min_value=1900, max_value=2030, value=1980)

    if st.button("Register"):
        register_user(username, password, user_gender, user_birth_year)


def check_user_exists(username):
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    return len(docs) >= 1


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
        db.collection("users").document(username).set(user_data)
        send_confirmation_email(username, username, confirmation_token)
        st.success("You have been sent an email - please click the link in the message to continue registration.")


def authenticate_user(username, password):

    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    if len(docs) == 1:
        stored_password = docs[0].to_dict()['password']
        return bcrypt.checkpw(
            password=password.encode('utf8'),
            hashed_password=stored_password.encode('utf8')
        )

    return False


def hash_password(password):
    hashed_password = bcrypt.hashpw(
        password.encode('utf8'), bcrypt.gensalt()
    ).decode('utf8')
    return hashed_password


def send_confirmation_email(send_to, username, confirmation_token):

    smtpserver = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    smtpserver.ehlo()
    smtpserver.login(st.secrets["email_address"], st.secrets["gmail_app_password"])

    subject = "Confirm Your Account Registration"
    body = f"Click the link below to confirm your registration:\n\n"
    confirmation_link = f"{st.secrets['app_url']}confirm?token={confirmation_token}&user={username}"
    body += confirmation_link
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = st.secrets["email_address"]
    msg['To'] = send_to

    smtpserver.send_message(msg)
    smtpserver.close()


def main():
    st.sidebar.title("Navigation")
    choice = st.sidebar.radio("Select an option:", ["Login", "Register"])

    # TODO: move this to within authenticated user code for security
    st.session_state['firestore'] = FirestoreWrapper()

    if choice == "Login":
        login()
    elif choice == "Register":
        register()


if __name__ == "__main__":
    hide()
    if is_authenticated():
        st.switch_page("./pages/user_home.py")
    main()

