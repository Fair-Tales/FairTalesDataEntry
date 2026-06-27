import streamlit as st
from datetime import datetime, timezone
from utilities import page_layout, check_authentication_status, FirestoreWrapper
from text_content import ReportFeedback

check_authentication_status()
page_layout(current_page="./pages/report_feedback.py")

st.title(ReportFeedback.page_title)
st.info(ReportFeedback.instruction)

with st.form("report_feedback_form"):
    feedback_type = st.radio(
        ReportFeedback.type_label,
        options=ReportFeedback.type_options,
        horizontal=True,
    )
    feedback_text = st.text_area(
        ReportFeedback.text_label,
        placeholder=ReportFeedback.text_placeholder,
    )
    submitted = st.form_submit_button(ReportFeedback.submit_label)

if submitted:
    if not feedback_text.strip():
        st.warning(ReportFeedback.empty_text_warning)
    else:
        fw = FirestoreWrapper(auth=True)
        username = st.session_state.get("username", "")
        user_ref = fw.username_to_doc_ref(username) if username else None
        db = fw.connect_book()
        db.collection("feedback").add(
            {
                "text": feedback_text.strip(),
                "type": feedback_type,
                "user": user_ref,
                "timestamp": datetime.now(timezone.utc),
            }
        )
        st.success(ReportFeedback.success_message)
