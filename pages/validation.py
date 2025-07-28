import streamlit as st
from utilities import page_layout, check_authentication_status

check_authentication_status()
page_layout()

st.info("Here you may validate inputted data")