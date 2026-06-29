import streamlit as st
from streamlit_option_menu import option_menu
from utilities import page_layout, check_authentication_status, navigate_to
from text_content import Instructions, PhotoUpload, Uploader, BookDataEntry
from photo_upload import (
    get_upload_session_id,
    generate_put_urls,
    build_uploader_html,
)

check_authentication_status()


def upload_page_photos():
    # Direct browser-to-S3 upload (#118): replaces st.file_uploader so the native
    # photo picker no longer drops the Streamlit websocket on mobile. This legacy
    # page has no implemented downstream yet (Save is a stub), so the uploader is
    # rendered here for consistency; photos land in uploads/data_entry/{session_id}/.
    st.write(Uploader.direct_upload_instructions)
    session_id = get_upload_session_id("data_entry")
    put_urls = generate_put_urls("data_entry", session_id)
    st.iframe(build_uploader_html(put_urls), height=460)


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

save_button = st.button(BookDataEntry.save_button, key="book_data_entry_save_button")

if save_button:
    st.warning(BookDataEntry.not_implemented)

