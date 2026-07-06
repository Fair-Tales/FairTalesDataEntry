import streamlit as st
from utilities import page_layout, FirestoreWrapper, normalize_username
from pages.uploader import upload_widget
from Home import ensure_session
from data_structures import Book
from text_content import Instructions, Alerts, QrLanding
from photo_upload import generate_put_urls, build_uploader_html

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
st.write(Instructions.photo_upload_instructions)
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
        put_urls = generate_put_urls(flow, session)
        st.iframe(build_uploader_html(put_urls), height=460)
        st.info(QrLanding.phone_done_instruction)
    else:
        # Legacy page-upload mode (page_photo_upload's QR): the phone runs the full
        # upload + processing pipeline against the QR's book.
        db_book = FirestoreWrapper().connect_book(auth=False)
        st.session_state['current_book'] = Book(
            db_book.collection('books').document(book).get().to_dict()
        )
        upload_widget(on_submit='close')
