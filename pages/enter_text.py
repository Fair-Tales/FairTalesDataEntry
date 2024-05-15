import streamlit as st
from utilities import hide, FormConfirmation
from PIL import Image
from streamlit_dimensions import st_dimensions
import s3fs
# TODO: use a method metadata_to_form to store session state entered data and re-display it in a form for revision?
# TODO: add capability to add or edit characters while paging through book.

hide()
NUMBER_OF_PAGES = 2

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
    elif st.session_state.current_page_number > NUMBER_OF_PAGES:
        st.session_state['current_page_number'] = NUMBER_OF_PAGES


story_page = st.checkbox(
    "Does this page contain story text?",
    value=False
)

col1, col2 = st.columns(2)
previous_page = col1.button("Previous page", use_container_width=True, key='b1')
next_page = col2.button("Next page", use_container_width=True, key='b2')

if next_page:
    page_change(1)
if previous_page:
    page_change(-1)

st.write("Showing page %d of %d." % (st.session_state.current_page_number, NUMBER_OF_PAGES))

col3, col4 = st.columns(2)
container_width = st_dimensions(key="main")['width']

# with fs.open(
#         "sawimages/temp_gruffalo_%d.png" % st.session_state.current_page_number,
#         mode='rb'
# ) as f:
#     w, h = Image.open(f).size
with fs.open(
        f"sawimages/{st.session_state['current_book'].title}/page_{st.session_state.current_page_number}.jpg",
        mode='rb'
) as f:
    w, h = Image.open(f).size


image_width = int(container_width/2)

col3.image(
    fs.open(
        f"sawimages/{st.session_state['current_book'].title}/page_{st.session_state.current_page_number}.jpg",
        mode='rb'
    ).read(),
    width=image_width
)
page_text = col4.text_area(
    "Enter page text",
    height=int(image_width*h/w),
    value="The Gruffalo looked angrily at the small mouse and thought to herself....",
    disabled=not story_page
)

save_button = st.button("Save button")
if save_button:
    st.warning("Not implemented yet!")

