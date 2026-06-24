import streamlit as st
from utilities import page_layout, FormConfirmation, check_authentication_status

check_authentication_status()
page_layout()

st.write(
    """
    Please double check the data you have entered. If you are happy click `Confirm`, otherwise click `Edit`
    to revise.
    """
)
getattr(FormConfirmation, FormConfirmation.forms[st.session_state.active_form_to_confirm])()
