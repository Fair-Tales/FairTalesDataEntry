import streamlit as st
from utilities import page_layout, check_authentication_status
from data_structures.user import User
from text_content import UserForm

check_authentication_status()
page_layout()

st.title(UserForm.page_header)

# Load the current user's Firestore document once per session.
# The User constructor sets is_registered=True so that any field
# assignment in to_form() immediately persists to Firestore.
if 'current_user' not in st.session_state:
    user_ref = st.session_state.firestore.username_to_doc_ref(
        st.session_state['username']
    )
    user_data = user_ref.get().to_dict()
    st.session_state['current_user'] = User(db_object=user_data)

with st.form("account_settings"):
    st.session_state['current_user'].to_form()
