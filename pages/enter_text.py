import streamlit as st
from PIL import Image
from streamlit_dimensions import st_dimensions
import s3fs
from utilities import page_layout, confirm_submit, check_authentication_status
from data_structures import Page, Character, Alias
from text_content import EnterText

check_authentication_status()

def create_page_dict_from_db():

    st.session_state['book_pages_dict'] = {
        page_num: Page(
            st.session_state.firestore.get_by_reference(
                collection='pages',
                document_ref=f"{st.session_state.current_book.document_id}_{page_num}"
            ).to_dict()
        )
        for page_num in range(1, st.session_state.current_book.page_count + 1)
    }

def page_change(delta):
    st.session_state.current_page_number += delta

    if st.session_state.current_page_number < 1:
        st.session_state['current_page_number'] = 1
    elif st.session_state.current_page_number > st.session_state.current_book.page_count:
        st.session_state['current_page_number'] = st.session_state.current_book.page_count


@st.cache_data(max_entries=3)
def load_image(book, page_number, use_cropped=True):
    """Load page image, preferring the corrected _cropped version when available."""
    if use_cropped:
        try:
            return Image.open(fs.open(
                f"sawimages/{book}/page_{page_number}_cropped.jpg", mode='rb'
            ))
        except Exception:
            pass
    return Image.open(fs.open(
        f"sawimages/{book}/page_{page_number}.jpg", mode='rb'
    ))


def cropped_exists(book, page_number):
    try:
        fs.info(f"sawimages/{book}/page_{page_number}_cropped.jpg")
        return True
    except Exception:
        return False


@st.dialog(" ", width="large")
def enlarged_image_dialog(use_cropped):
    st.image(
        load_image(
            st.session_state['current_book'].title,
            st.session_state.current_page_number,
            use_cropped=use_cropped,
        ),
        width="stretch"
    )


def display_image():
    book = st.session_state['current_book'].title
    page_number = st.session_state.current_page_number
    has_cropped = cropped_exists(book, page_number)

    use_cropped = True
    if has_cropped:
        use_cropped = not col1.toggle(
            "Show original photo", value=False, key=f"show_raw_{page_number}"
        )
        if not use_cropped:
            col1.caption("Showing original photo")
        else:
            col1.caption("✓ Auto-corrected")
    else:
        col1.caption("⚠ Auto-correction unavailable — showing original photo")

    page_image = load_image(book, page_number, use_cropped=use_cropped)
    w, h = page_image.size

    col1.image(page_image, width="stretch")
    if col1.button("🔍 Enlarge", width="stretch"):
        enlarged_image_dialog(use_cropped=use_cropped)

    dimensions = st_dimensions(key="main")
    col_width = int(dimensions['width'] * 3 / 5) if dimensions else 500
    return int(col_width * h / w)

def adding_character():
    if st.session_state['_page_text_editing'] is not None:
        st.session_state.current_page.text = st.session_state['_page_text_editing']
    st.session_state.now_entering = 'character'


def adding_text():
    st.session_state.now_entering = 'text'
    st.session_state.pop('current_alias', None)


def adding_alias():
    if st.session_state['_page_text_editing'] is not None:
        st.session_state.current_page.text = st.session_state['_page_text_editing']
    st.session_state['_alias_form_count'] = st.session_state.get('_alias_form_count', 0) + 1
    st.session_state.now_entering = 'alias'


def text_entry(element, image_height, delta=50):
    st.session_state.current_page.contains_story = element.checkbox(
        "Does this page contain story text?",
        value=st.session_state.current_page.contains_story
    )
    subcol1, subcol2 = element.columns(2)
    subcol1.button("Add character", width="stretch", on_click=adding_character, help=EnterText.character_help)
    subcol2.button("Add alias", width="stretch", on_click=adding_alias, help=EnterText.alias_help)

    height = max(image_height - delta, 200)

    with element.form('page_text'):
        st.session_state['_page_text_editing'] = st.text_area(
            "Enter page text",
            height=height,
            value=st.session_state.current_page.text,
            disabled=not st.session_state.current_page.contains_story
        )

        submitted = st.form_submit_button("Save page")
        if submitted:
            st.session_state.current_page.text = st.session_state['_page_text_editing']


def character_entry(element):

    st.session_state['current_character'] = Character(
        book=st.session_state['current_book'].title
    )
    with element.form('character'):
        st.session_state['current_character'].to_form()

    element.button('Cancel adding character', width="stretch", on_click=adding_text)


def alias_entry(element):

    st.session_state['current_alias'] = Alias(
        book=st.session_state['current_book'].title
    )
    form_key = f"alias_{st.session_state.get('_alias_form_count', 0)}"
    with element.form(form_key):
        st.session_state['current_alias'].to_form()

    element.button('Cancel adding alias', width="stretch", on_click=adding_text)


def user_entry_box(element, image_height, delta=50):
    if st.session_state.now_entering == 'text':
        text_entry(element, image_height, delta)
    elif st.session_state.now_entering == 'character':
        character_entry(element)
    elif st.session_state.now_entering == 'alias':
        alias_entry(element)

# def create_current_page_from_db():
#     st.session_state.current_page = Page(
#         st.session_state.firestore.get_by_reference(
#             collection='pages',
#             document_ref=f"{st.session_state.current_book.document_id}_{st.session_state.current_page_number}"
#         ).to_dict()
#     )


page_layout()

fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

if 'book_pages_dict' not in st.session_state:
    create_page_dict_from_db()

if 'current_page_number' not in st.session_state:
    st.session_state['current_page_number'] = 1

if 'now_entering' not in st.session_state:
    st.session_state['now_entering'] = 'text'

# create_current_page_from_db()
st.session_state.current_page = st.session_state.book_pages_dict[
    st.session_state.current_page_number
]

st.header(EnterText.header)
st.write(EnterText.instruction)

col1, col2 = st.columns([3, 2])
previous_page = col1.button("Previous page", width="stretch", key='b1', on_click=page_change, args=(-1,))
next_page = col2.button("Next page", width="stretch", key='b2', on_click=page_change, args=(1,))

col1.write(
    "Showing page %d of %d."
    % (st.session_state.current_page_number, st.session_state.current_book.page_count)
)
image_height = display_image()

if '_page_text_editing' not in st.session_state:
    st.session_state['_page_text_editing'] = None

user_entry_box(col2, image_height)

butcol1, butcol2 = st.columns(2)
return_button = butcol1.button("Back to menu", width="stretch")
save_button = butcol2.button("Finish and submit book", help=EnterText.save_help, width="stretch")

if return_button:
    st.switch_page("./pages/book_edit_home.py")

if save_button:
    confirm_submit()


