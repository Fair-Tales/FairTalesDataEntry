import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import Validation

check_authentication_status()
page_layout()

st.info(Validation.intro)