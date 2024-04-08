import streamlit as st
from utilities import hide, FormConfirmation

# TODO: use a method metadata_to_form to store session state entered data and re-display it in a form for revision?

hide()

st.write(
    """
    Please double check the data you have entered. If you are happy click `Confirm`, otherwise click `Edit`
    to revise.
    """
)
getattr(FormConfirmation, FormConfirmation.forms[st.session_state.active_form_to_confirm])()
