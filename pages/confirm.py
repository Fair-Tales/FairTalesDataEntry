import streamlit as st
from utilities import FirestoreWrapper, page_layout
from text_content import Confirm

page_layout()

token = st.query_params.token
user = st.query_params.user

db = FirestoreWrapper().connect_user(auth=False)
user_ref = db.collection("users").document(user)
user_data = user_ref.get().to_dict()

if user_data['is_confirmed']:
    st.warning(Confirm.already_confirmed)
else:
    try:
        if token == user_data['confirmation_token']:
            user_ref.update({
                'is_confirmed': True
            })
            st.success(
                Confirm.success
            )
        else:
            st.error(Confirm.invalid_link)
    except Exception:
        st.error(Confirm.failed)
