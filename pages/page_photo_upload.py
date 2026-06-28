import s3fs
import streamlit as st
from six import BytesIO
from streamlit_option_menu import option_menu
from text_content import Alerts, PhotoUpload
from utilities import page_layout, check_authentication_status
from text_content import Instructions
from data_structures import Page
from pages.uploader import upload_widget
import qrcode
from requests.models import PreparedRequest

check_authentication_status()

def go_to_phone():
    st.write(Instructions.go_to_phone_instructions)
    # We build a URL to send the user to the photo upload page on their phone via QR code...
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    url = f"{st.secrets.app_url}qr_landing"
    user = st.session_state.username
    token = st.session_state.firestore.username_to_doc_ref(user).get().to_dict()['confirmation_token']
    params = {
        'user': st.session_state.username,
        'token': token,
        'book': st.session_state.current_book.document_id
    }
    req = PreparedRequest()
    req.prepare_url(url, params)
    qr.add_data(req.url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    temp = BytesIO()
    img.save(temp)
    st.image(temp.getvalue())
    st.write(PhotoUpload.link_line % (req.url, req.url))

    st.write(PhotoUpload.finished_instruction)

    subcol1, subcol2 = st.columns(2)
    if subcol1.button(PhotoUpload.continue_button, width="stretch"):
        if st.session_state.current_book.photos_uploaded:
            st.session_state['current_page_number'] = 1
            st.switch_page("./pages/enter_text.py")
        else:
            st.warning(Alerts.please_uploaded_photos)
    if subcol2.button(PhotoUpload.back_to_menu_button, width="stretch"):
        st.switch_page("./pages/book_edit_home.py")


def upload_here():
    st.write(Instructions.upload_here_instructions)
    upload_widget()


def already_uploaded_options():
    """Let the user skip the upload step or choose to replace existing photos."""
    st.info(Instructions.photos_already_uploaded)
    skip_col, replace_col = st.columns(2)
    if skip_col.button(PhotoUpload.continue_to_text_button, width="stretch"):
        st.session_state.pop('_replacing_photos', None)
        st.session_state['current_page_number'] = 1
        st.switch_page("./pages/enter_text.py")
    if replace_col.button(PhotoUpload.replace_button, width="stretch"):
        # Scope the override to this book so re-opening a *different* book still
        # offers the skip choice rather than dropping straight into uploading.
        st.session_state['_replacing_photos'] = st.session_state.current_book.document_id
        st.rerun()
    if st.button(PhotoUpload.back_to_menu_button, width="stretch"):
        st.switch_page("./pages/book_edit_home.py")


def show_upload_options():
    st.header(Instructions.photo_upload_header)
    st.write(Instructions.photo_upload_instructions)

    # When photos were already captured via the photo-first flow (#59), default
    # to "Upload here" — that path reuses the stashed photos rather than
    # re-uploading them.
    selected_option = option_menu(
        None, ["Go to phone", "Upload here"],
        default_index=1 if st.session_state.get('photo_first_pages') else 0,
        icons=['phone', 'laptop'],
        menu_icon="cast", orientation="horizontal",
        key="upload_menu",
        styles={
            "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
            "nav-link-selected": {"background-color": "green"},
        }
    )

    navigation_dict = {
        "Go to phone": go_to_phone,
        "Upload here": upload_here
    }

    navigation_dict[selected_option]()


page_layout()

st.title(
    PhotoUpload.enter_book_data_title.format(title=st.session_state.current_book.title)
)

# If photos already exist for this book, offer to skip to text entry or to
# replace them, rather than forcing the user back through the upload step.
_book_id = st.session_state.current_book.document_id
if (
    st.session_state.current_book.photos_uploaded
    and st.session_state.get('_replacing_photos') != _book_id
):
    already_uploaded_options()
else:
    show_upload_options()












