import streamlit as st
from utilities import page_layout, check_authentication_status, is_team_or_above
from text_content import Validation

check_authentication_status()

# Validation is a team-member-and-above workflow (#83). The workflow itself is
# still to be built (#47); this page only gates access to it for now.
if not is_team_or_above():
    st.error(Validation.not_authorised)
    st.stop()

page_layout()

st.info(Validation.intro)
