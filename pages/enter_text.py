import streamlit as st
from utilities import hide, FormConfirmation
from PIL import Image
from streamlit_dimensions import st_dimensions
import s3fs

# TODO: also set Book.page_count at the same time
# TODO: get NUMBER_OF_PAGES from Book, get page contains story from Page
# TODO: button to replace page text entry with character add form (write character data structure)
# TODO: button to replace page text entry with alias add form (write alias data structure)
# TODO: delete character or alias?

hide()

fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

if 'current_page_number' not in st.session_state:
    st.session_state['current_page_number'] = 1

st.header("Please enter the text for page:")


def page_change(delta):

    st.session_state.current_page_number += delta

    if st.session_state.current_page_number < 1:
        st.session_state['current_page_number'] = 1
    elif st.session_state.current_page_number > st.session_state.current_book.page_count:
        st.session_state['current_page_number'] = st.session_state.current_book.page_count


@st.cache_data(max_entries=3)
def load_image():
    return Image.open(fs.open(
            f"sawimages/{st.session_state['current_book'].title}/page_{st.session_state.current_page_number}.jpg",
            mode='rb'
        ))


def display_image():
    page_image = load_image()
    w, h = page_image.size

    container_width = st_dimensions(key="main")['width']
    _image_width = int(container_width / 2)

    col1.write("# ")
    col1.image(
        page_image,
        width=_image_width
    )
    scaled_height = int(_image_width*h/w)
    return scaled_height


col1, col2 = st.columns(2)
previous_page = col1.button("Previous page", use_container_width=True, key='b1')
next_page = col2.button("Next page", use_container_width=True, key='b2')

if next_page:
    page_change(1)
if previous_page:
    page_change(-1)

col1.write(
    "Showing page %d of %d."
    % (st.session_state.current_page_number, st.session_state.current_book.page_count)
)
story_page = col2.checkbox(
    "Does this page contain story text?",
    value=False
)
image_height = display_image()

page_text = col2.text_area(
    "Enter page text",
    height=image_height,
    value="The Gruffalo looked angrily at the small mouse and thought to herself....",
    disabled=not story_page
)

save_button = st.button("Save button")
if save_button:
    st.warning("Not implemented yet!")

