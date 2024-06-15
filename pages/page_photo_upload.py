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
    params = {
        'user': st.session_state.username,
        'token': 42,
        'book': 'test'
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


def upload_here():
    st.write(Instructions.upload_here_instructions)
    fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

    # TODO: change per file size limit?!
    # TODO: set file order (sort ascending? time modified? https://stackoverflow.com/questions/31588543/how-to-change-order-of-files-in-multiple-file-input)
    def upload_page_photos():
        uploaded_files = st.file_uploader("Select page photos to upload", accept_multiple_files=True)

        if uploaded_files:
            st.write("Saving page photos to the database, please stay on this page...")
            photos_url = f"sawimages/{st.session_state['current_book'].title}"

            for fi, uploaded_file in enumerate(uploaded_files):
                page_number = fi + 1
                with fs.open(
                        photos_url + f"/page_{page_number}.jpg",
                        'wb'
                ) as out_file:
                    out_file.write(uploaded_file.read())

                page = Page(
                    page_number=page_number,
                    book=st.session_state['current_book'].title
                )
                page.register()

            st.session_state.current_book.photos_uploaded = True
            st.session_state.current_book.photos_url = photos_url
            st.session_state.current_book.page_count = len(uploaded_files)

            st.write("Page photo upload complete, you may continue.")
            submit = st.button('Continue')

            if submit:
                st.switch_page("./pages/enter_text.py")

    upload_page_photos()


navigation_dict = {
    "Go to phone": go_to_phone,
    "Upload here": upload_here
}

navigation_dict[selected_option]()







