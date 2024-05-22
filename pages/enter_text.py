import streamlit as st
from PIL import Image
from streamlit_dimensions import st_dimensions
import s3fs
st.set_page_config(
    # layout="wide",
    initial_sidebar_state='collapsed'
)
from utilities import hide
from data_structures import Page, Character, Alias
from text_content import EnterText


# TODO: write character and alias data structures
# (TODO: make forms work for c and a)
# TODO: button to replace page text entry with alias add form (write alias data structure)
# TODO: delete character or alias?
# TODO: add splitlines and replace tabs to text entry

hide()

fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )


def create_current_page_from_db():
    st.session_state.current_page = Page(
        st.session_state.firestore.get_by_reference(
            collection='pages',
            document_ref=f"{st.session_state.current_book.document_id}_{st.session_state.current_page_number}"
        ).to_dict()
    )


if 'current_page_number' not in st.session_state:
    st.session_state['current_page_number'] = 1

if 'now_entering' not in st.session_state:
    st.session_state['now_entering'] = 'text'

create_current_page_from_db()

st.header(EnterText.header)
st.write(EnterText.instruction)


def page_change(delta):
    st.session_state.current_page_number += delta

    if st.session_state.current_page_number < 1:
        st.session_state['current_page_number'] = 1
    elif st.session_state.current_page_number > st.session_state.current_book.page_count:
        st.session_state['current_page_number'] = st.session_state.current_book.page_count


@st.cache_data(max_entries=3)
def load_image(book, page_number):
    return Image.open(fs.open(
            f"sawimages/{book}/page_{page_number}.jpg",
            mode='rb'
        ))


def display_image():
    page_image = load_image(
        st.session_state['current_book'].title,
        st.session_state.current_page_number
    )
    w, h = page_image.size

    dimensions = st_dimensions(key="main")
    container_width = dimensions['width'] if dimensions is not None else 10
    _image_width = int(container_width / 2)

    col1.write("# ")
    col1.image(
        page_image,
        width=_image_width
    )
    scaled_height = int(_image_width*h/w)
    return scaled_height


col1, col2 = st.columns(2)
previous_page = col1.button("Previous page", use_container_width=True, key='b1', on_click=page_change, args=(-1,))
next_page = col2.button("Next page", use_container_width=True, key='b2', on_click=page_change, args=(1,))

col1.write(
    "Showing page %d of %d."
    % (st.session_state.current_page_number, st.session_state.current_book.page_count)
)
image_height = display_image()


def adding_character():
    st.session_state.now_entering = 'character'


def adding_text():
    st.session_state.now_entering = 'text'


def adding_alias():
    st.session_state.now_entering = 'alias'


def text_entry(element, image_height, delta=50):
    st.session_state.current_page.contains_story = element.checkbox(
        "Does this page contain story text?",
        value=st.session_state.current_page.contains_story
    )
    subcol1, subcol2 = element.columns(2)
    subcol1.button("Add character", use_container_width=True, on_click=adding_character, help=EnterText.character_help)
    subcol2.button("Add alias", use_container_width=True, on_click=adding_alias, help=EnterText.alias_help)

    height = max(image_height - delta, 10)
    st.session_state.current_page.text = element.text_area(
        "Enter page text",
        height=height,
        value=st.session_state.current_page.text,
        disabled=not st.session_state.current_page.contains_story
    )


def character_entry(element):

    st.session_state['current_character'] = Character(
        book=st.session_state['current_book'].title
    )
    with element.form('character'):
        st.session_state['current_character'].to_form()

    element.button('Cancel adding character', use_container_width=True, on_click=adding_text)


def alias_entry(element):

    st.session_state['current_alias'] = Alias(
        book=st.session_state['current_book'].title
    )
    with element.form('alias'):
        st.session_state['current_alias'].to_form()

    element.button('Cancel adding alias', use_container_width=True, on_click=adding_text)


def user_entry_box(element, image_height, delta=50):
    if st.session_state.now_entering == 'text':
        text_entry(element, image_height, delta)
    elif st.session_state.now_entering == 'character':
        character_entry(element)
    elif st.session_state.now_entering == 'alias':
        alias_entry(element)


user_entry_box(col2, image_height)
save_button = st.button("Finish entering book", help=EnterText.save_help, use_container_width=True)
if save_button:
    st.switch_page("./pages/user_home.py")

