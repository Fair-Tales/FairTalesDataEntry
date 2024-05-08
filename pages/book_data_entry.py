import streamlit as st
from streamlit_option_menu import option_menu
from utilities import hide
from text_content import Instructions


hide()
st.title(
    f"Enter book data: {st.session_state.book_metadata['title']}"
)
st.write(Instructions.data_entry_instructions)

selected_option = option_menu(
    None, ["Upload page photos", "Enter text", "Add a Character"],
    default_index=0,
    icons=['search', 'search', 'database-add'],
    menu_icon="cast", orientation="horizontal",
    key="user_option_menu"
)


# TODO: change per file size limit?!
# TODO: set file order (sort ascending? time modified? https://stackoverflow.com/questions/31588543/how-to-change-order-of-files-in-multiple-file-input)
def upload_page_photos():
    uploaded_files = st.file_uploader("Select page photos to upload", accept_multiple_files=True)
    # for uploaded_file in uploaded_files:
    #     bytes_data = uploaded_file.read()
    #     st.write("filename:", uploaded_file.name)
    #     #st.write(bytes_data)


def enter_text():
    st.switch_page("./pages/enter_text.py")


def add_character():
    st.switch_page("./pages/add_character.py")


navigation_dict = {
    "Upload page photos": upload_page_photos,
    "Enter text": enter_text,
    "Add a Character": add_character
}

navigation_dict[selected_option]()


save_button = st.button('Save')

if save_button:
    st.warning("Not implemented yet!")

