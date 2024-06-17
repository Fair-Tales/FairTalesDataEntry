import s3fs
import streamlit as st
from six import BytesIO
from streamlit_option_menu import option_menu

st.set_page_config(
    page_title="bde",
    initial_sidebar_state="collapsed"
)
from utilities import hide
from text_content import Instructions
from data_structures import Page
from pages.uploader import upload_widget
import qrcode
from requests.models import PreparedRequest


hide()
st.title(
    f"Enter book data: {st.session_state.current_book.title}"
)
st.header(Instructions.photo_upload_header)
st.write(Instructions.photo_upload_instructions)

selected_option = option_menu(
    None, ["Go to phone", "Upload here"],
    default_index=0,
    icons=['phone', 'laptop'],
    menu_icon="cast", orientation="horizontal",
    key="upload_menu",
    styles={
        "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
        "nav-link-selected": {"background-color": "green"},
    }
)


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
    st.write("Or you can use the following link: [%s](%s)" % (req.url, req.url))
    if st.button("When you have finished you can click here to go back."):
        st.switch_page("./pages/book_edit_home.py")


def upload_here():
    st.write(Instructions.upload_here_instructions)
    upload_widget()


navigation_dict = {
    "Go to phone": go_to_phone,
    "Upload here": upload_here
}

navigation_dict[selected_option]()












