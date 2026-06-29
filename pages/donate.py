import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import Donate

check_authentication_status()
page_layout(current_page="./pages/donate.py")

st.header(Donate.header)
st.write(Donate.body)
st.markdown(f"[{Donate.link_text}]({Donate.url})")
st.caption(Donate.caption)
