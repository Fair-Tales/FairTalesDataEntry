import streamlit as st
from utilities import check_authentication_status, page_layout, clear_page_history, navigate_to
from text_content import Instructions

check_authentication_status()

clear_page_history()
page_layout(current_page="./pages/landing.py")

st.title("Fair Tales Data Entry Tool")
st.write(Instructions.landing_intro)

col1, col2 = st.columns(2)

with col1:
    st.subheader(Instructions.landing_enter_data_label)
    st.write(Instructions.landing_enter_data_description)
    if st.button(Instructions.landing_enter_data_label, use_container_width=True):
        navigate_to("./pages/user_home.py")

with col2:
    st.subheader(Instructions.landing_view_results_label)
    st.write(Instructions.landing_view_results_description)
    if st.button(Instructions.landing_view_results_label, use_container_width=True):
        navigate_to("./pages/results_dashboard.py")
