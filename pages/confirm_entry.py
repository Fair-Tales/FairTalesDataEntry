import streamlit as st
from utilities import hide, FormConfirmation

hide()

st.write(
    """
    Please double check the data you have entered. If you are happy click `Confirm`, otherwise click `Edit`
    to revise.
    """
)
getattr(FormConfirmation, FormConfirmation.forms[st.session_state.active_form_to_confirm])()
