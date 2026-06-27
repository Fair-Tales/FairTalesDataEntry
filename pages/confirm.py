import streamlit as st
from utilities import FirestoreWrapper, page_layout

page_layout()

token = st.query_params.token
user = st.query_params.user

db = FirestoreWrapper().connect_user(auth=False)
user_ref = db.collection("users").document(user)
user_data = user_ref.get().to_dict()

if user_data['is_confirmed']:
    st.warning("User account already confirmed. Please proceed to login by selecting `Home` in navigation menu.")
else:
    try:
        if token == user_data['confirmation_token']:
            user_ref.update({
                'is_confirmed': True
            })
            st.success(
                "User registration successful! You can now proceed to login by selecting `Home` from the navigation menu."
            )
        else:
            st.error("Invalid or expired confirmation link. Please request a new confirmation email.")
    except Exception:
        st.error("Registration failed. Please try again.")
