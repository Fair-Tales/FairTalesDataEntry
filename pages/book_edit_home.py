import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions
from utilities import check_authentication_status, hide, confirm_submit


hide()
st.title(f"Editing book: {st.session_state.current_book.title}")

check_authentication_status()

edit_option = option_menu(
    None, ["Instructions", "Edit metadata", "Upload photos", "Enter text"],
    default_index=0,
    icons=['info-circle', 'list-stars', 'image', 'pencil-square'],
    menu_icon="cast", orientation="horizontal",
    key="book_edit_option_menu",
    styles={
        "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
        "nav-link-selected": {"background-color": "green"},
    }
)


def instructions():
    st.write(Instructions.book_edit_home_intro)
    st.write(Instructions.book_edit_home_instructions)


def edit_book_details():
    st.session_state.current_book.editing = True
    st.switch_page("./pages/add_book.py")


def add_photos():
    st.switch_page("./pages/page_photo_upload.py")


def enter_text():
    if st.session_state.current_book.photos_uploaded:
        st.session_state['current_page_number'] = 1
        st.switch_page("./pages/enter_text.py")
    else:
        st.warning(Alerts.please_uploaded_photos)


edit_navigation_dict = {
    "Instructions": instructions,
    "Edit metadata": edit_book_details,
    "Upload photos": add_photos,
    "Enter text": enter_text
}

edit_navigation_dict[edit_option]()

if st.button("Back to home menu."):
    st.switch_page("./pages/user_home.py")

if st.button("Finish and submit book"):
    confirm_submit()


