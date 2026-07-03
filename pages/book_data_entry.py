import streamlit as st
from streamlit_option_menu import option_menu
from utilities import page_layout, check_authentication_status, navigate_to
from text_content import Instructions, PhotoUpload, BookDataEntry

check_authentication_status()


def upload_page_photos():
    # Route into the real working photo pipeline (#130): page_photo_upload.py
    # drives uploader.upload_widget (orientation-correct/crop/OCR -> sawimages/
    # {title}/ + Page records) and offers the QR-to-phone option, working off
    # st.session_state.current_book which this hub sets.
    navigate_to("./pages/page_photo_upload.py")


def enter_text():
    navigate_to("./pages/enter_text.py")


def add_character():
    navigate_to("./pages/add_character.py")


page_layout(current_page="./pages/book_data_entry.py")

st.title(
    PhotoUpload.enter_book_data_title.format(title=st.session_state.current_book.title)
)
st.header(Instructions.photo_upload_header)
st.write(Instructions.photo_upload_instructions)
st.write(Instructions.photo_naming_instructions)

selected_option = option_menu(
    None, [BookDataEntry.menu_upload_photos, BookDataEntry.menu_enter_text, BookDataEntry.menu_add_character],
    default_index=0,
    icons=['search', 'search', 'database-add'],
    menu_icon="cast", orientation="horizontal",
    key="user_option_menu"
)

navigation_dict = {
    BookDataEntry.menu_upload_photos: upload_page_photos,
    BookDataEntry.menu_enter_text: enter_text,
    BookDataEntry.menu_add_character: add_character
}

navigation_dict[selected_option]()

