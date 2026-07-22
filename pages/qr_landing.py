import streamlit as st
from utilities import page_layout, FirestoreWrapper, normalize_username, get_s3_filesystem
from pages.uploader import upload_widget
from Home import ensure_session
from data_structures import Book
from text_content import Instructions, Alerts, QrLanding
from photo_upload import (
    render_uploader, render_photo_instructions, render_uploaded_photos_list,
    cleanup_prefix, invalidate_uploader_state,
)

# NB: this is a PUBLIC, token-authenticated deep-link reached from the phone QR —
# it must NOT call check_authentication_status()/redirect to login. It does its
# OWN auth below from the ?user=&token= query params (the phone has no session
# cookie), then shows the seamless upload UI. A stray login gate here bounced the
# phone to the login page before the token could authenticate it.
page_layout()

# Auth params are always present; ``flow``/``session`` (new generic mode, #143)
# and ``book`` (legacy page-upload mode) are optional, so read them defensively.
token = st.query_params.get("token")
# Normalize (#129 shared helper): the phone QR link is built from the
# (already-normalized) session username, but normalize defensively here too so
# a differently-cased ``user`` still resolves to the same account doc.
user = normalize_username(st.query_params.get("user"))
book = st.query_params.get("book")
flow = st.query_params.get("flow")
session = st.query_params.get("session")

st.title(QrLanding.title)
# Canonical photo guidance on the phone QR flow too (#186) — users reaching the
# uploader here previously saw none of the framing/order/lighting advice.
render_photo_instructions(expanded=True)
st.write(Instructions.photo_naming_instructions)

db_user = FirestoreWrapper().connect_user(auth=False)
user_doc = (
    db_user.collection("users").document(user).get().to_dict() if user else None
)
authorised = bool(
    user_doc and token and token == user_doc.get("confirmation_token")
)

if not authorised:
    st.warning(Alerts.invalid_credentials)
else:
    # Home.py's ensure_session() has already populated firestore + lookup dicts
    # before this page ran; this call is an idempotent no-op kept for the case
    # where the page is reached without that guarantee.
    ensure_session()
    st.session_state['authentication_status'] = True
    st.session_state['username'] = user

    if flow and session:
        # Generic direct-to-S3 mode (#143): the phone PUTs each photo straight into
        # the SAME temp prefix uploads/{flow}/{session}/ that the computer surface
        # (single / batch / collection) is reading. No book context is needed — the
        # phone only uploads; the computer does the processing when the user returns
        # and taps its read button. Reuses the shared one-way uploader component.
        fs = get_s3_filesystem()
        # Shared uploader recipe (#129): cached presigned URLs (byte-stable
        # iframe HTML across reruns, so a rerun can't remount the iframe and
        # reset its slot state mid-upload) + the prefix's existing-slot seed
        # (a reloaded phone resumes the prefix instead of colliding with it).
        render_uploader(fs, flow, session)
        st.info(QrLanding.phone_done_instruction)

        # Live "uploaded so far" list (#186) so the user can SEE what has landed
        # and spot accidental duplicates. Polls the temp prefix every few seconds.
        @st.fragment(run_every=3)
        def _uploaded_list(flow=flow, session=session):
            render_uploaded_photos_list(get_s3_filesystem(), flow, session)

        _uploaded_list()

        # "Start over" (upload-duplication fix): a half-failed upload on flaky
        # WiFi previously had NO phone-side escape hatch — retrying poured the
        # re-selected photos into the dirty prefix as extra pages. Two-tap
        # delete: the first button arms a confirm, which does the cleanup and
        # invalidates the cached uploader state so a clean iframe mounts.
        if st.session_state.get('_qr_clear_armed'):
            st.warning(QrLanding.clear_confirm_warning)
            confirm_col, keep_col = st.columns(2)
            if confirm_col.button(QrLanding.clear_confirm_button,
                                  key="qr_landing_clear_confirm_button"):
                cleanup_prefix(fs, flow, session)
                invalidate_uploader_state(flow)
                st.session_state.pop('_qr_clear_armed', None)
                st.session_state['_qr_cleared'] = True
                st.rerun()
            if keep_col.button(QrLanding.clear_cancel_button,
                               key="qr_landing_clear_cancel_button"):
                st.session_state.pop('_qr_clear_armed', None)
                st.rerun()
        else:
            if st.session_state.pop('_qr_cleared', None):
                st.info(QrLanding.cleared_info)
            if st.button(QrLanding.clear_button, key="qr_landing_clear_button"):
                st.session_state['_qr_clear_armed'] = True
                st.rerun()
    else:
        # Legacy page-upload mode (page_photo_upload's QR): the phone runs the full
        # upload + processing pipeline against the QR's book.
        db_book = FirestoreWrapper().connect_book(auth=False)
        st.session_state['current_book'] = Book(
            db_book.collection('books').document(book).get().to_dict()
        )
        upload_widget(on_submit='close')
