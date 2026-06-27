import streamlit as st
from utilities import page_layout, check_authentication_status

check_authentication_status()
page_layout()

st.info("Here you may change your account settings...")

st.divider()

st.markdown(
    "**Support this project** — If you'd like to help cover API and hosting costs, "
    "a small contribution goes a long way. "
    "[Donate on Ko-fi ☕](https://ko-fi.com/PLACEHOLDER)"  # TODO: confirm donation platform/URL
)
st.caption(
    "Contributions are entirely optional and deeply appreciated. "
    "Every book you enter already makes a difference."
)