import streamlit as st
from utilities import hide

hide()
token = st.query_params.token
user = st.query_params.user
book = st.query_params.user

st.write(f"You have landed as user {user} and your token is {token}.")
