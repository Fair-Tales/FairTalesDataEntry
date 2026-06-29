import streamlit as st
from streamlit_option_menu import option_menu
from utilities import page_layout, check_authentication_status, navigate_to
from text_content import Instructions, PhotoUpload, Uploader, BookDataEntry

check_authentication_status()

# TODO: change per file size limit?!
# TODO: set file order (sort ascending? time modified? https://stackoverflow.com/questions/31588543/how-to-change-order-of-files-in-multiple-file-input)
def upload_page_photos():
    uploaded_files = st.file_uploader(Uploader.select_photos_label, accept_multiple_files=True, key="book_data_entry_uploader")
    # for uploaded_file in uploaded_files:
    #     bytes_data = uploaded_file.read()
    #     st.write("filename:", uploaded_file.name)
    #     #st.write(bytes_data)


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

