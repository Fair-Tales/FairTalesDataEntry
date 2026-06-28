import io

import streamlit as st
from PIL import Image
from streamlit_dimensions import st_dimensions
import s3fs
from utilities import page_layout, confirm_submit, check_authentication_status
from data_structures import Page, Character, Alias
from text_content import EnterText, ManageCharacters, AliasForm

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
        except FileNotFoundError:
            # No corrected version exists yet; fall back to the original below.
            pass
    return Image.open(fs.open(
        f"sawimages/{book}/page_{page_number}.jpg", mode='rb'
    ))


def cropped_exists(book, page_number):
    try:
        fs.info(f"sawimages/{book}/page_{page_number}_cropped.jpg")
        return True
    except FileNotFoundError:
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


@st.dialog("Edit image", width="large")
def manual_correction_dialog():
    book = st.session_state['current_book'].title
    page_number = st.session_state.current_page_number

    raw_image = load_image(book, page_number, use_cropped=False)

    if '_manual_rotation' not in st.session_state:
        st.session_state['_manual_rotation'] = 0

    st.subheader("Rotation")
    col_a, col_b, col_c, _ = st.columns(4)
    rotate_90_left = col_a.button("↺ 90° left")
    rotate_90_right = col_b.button("↻ 90° right")
    rotate_180 = col_c.button("180°")

    if rotate_90_left:
        st.session_state['_manual_rotation'] -= 90
    if rotate_90_right:
        st.session_state['_manual_rotation'] += 90
    if rotate_180:
        st.session_state['_manual_rotation'] += 180

    fine_angle = st.slider("Fine adjustment (degrees)", -45, 45, 0, key="fine_rotation")

    st.subheader("Crop margins (%)")
    crop_left = st.slider("Left", 0, 40, 0, key="crop_left")
    crop_right = st.slider("Right", 0, 40, 0, key="crop_right")
    crop_top = st.slider("Top", 0, 40, 0, key="crop_top")
    crop_bottom = st.slider("Bottom", 0, 40, 0, key="crop_bottom")

    img = raw_image.copy()
    total_angle = st.session_state['_manual_rotation'] + fine_angle

    if total_angle != 0:
        img = img.rotate(-total_angle, expand=True)

    w, h = img.size
    if crop_left + crop_right < 100 and crop_top + crop_bottom < 100:
        left = int(w * crop_left / 100)
        right = int(w * (1 - crop_right / 100))
        top_px = int(h * crop_top / 100)
        bottom_px = int(h * (1 - crop_bottom / 100))
        if right > left and bottom_px > top_px:
            img = img.crop((left, top_px, right, bottom_px))

    st.image(img, width="stretch", caption="Preview")

    save_col, discard_col = st.columns(2)
    if save_col.button("💾 Save as corrected image", width="stretch"):
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=95)
        cropped_path = f"sawimages/{book}/page_{page_number}_cropped.jpg"
        with fs.open(cropped_path, 'wb') as f:
            f.write(buf.getvalue())
        load_image.clear()
        # Close the dialog and rerun the main page so the corrected image is
        # shown immediately (st.rerun() inside an st.dialog dismisses it).
        st.rerun()

    if discard_col.button("✕ Discard", width="stretch"):
        st.rerun()


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
        if col1.button("✏ Edit image", width="stretch"):
            # Start each editing session from a clean slate: clear any rotation/
            # crop state left from a previous open (including closing via the
            # dialog's native ✕, which we can't otherwise hook into).
            for _k in (
                '_manual_rotation', 'fine_rotation',
                'crop_left', 'crop_right', 'crop_top', 'crop_bottom',
            ):
                st.session_state.pop(_k, None)
            manual_correction_dialog()

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


def managing_characters():
    if st.session_state['_page_text_editing'] is not None:
        st.session_state.current_page.text = st.session_state['_page_text_editing']
    st.session_state.now_entering = 'manage'


@st.dialog(ManageCharacters.delete_character_dialog_title)
def confirm_delete_character(character_doc_dict, name):
    st.write(ManageCharacters.delete_character_warning.format(name=name))
    if st.button(ManageCharacters.confirm_delete_button):
        Character(db_object=character_doc_dict).delete()
        st.session_state.now_entering = 'manage'
        st.rerun()
    if st.button(ManageCharacters.cancel_button):
        st.rerun()


@st.dialog(ManageCharacters.delete_alias_dialog_title)
def confirm_delete_alias(alias_doc_dict, name):
    st.write(ManageCharacters.delete_alias_warning.format(name=name))
    if st.button(ManageCharacters.confirm_delete_button):
        Alias(db_object=alias_doc_dict).delete()
        st.session_state.now_entering = 'manage'
        st.rerun()
    if st.button(ManageCharacters.cancel_button):
        st.rerun()


def text_entry(element, image_height, delta=50):
    st.session_state.current_page.contains_story = element.checkbox(
        "Does this page contain story text?",
        value=st.session_state.current_page.contains_story
    )
    subcol1, subcol2 = element.columns(2)
    subcol1.button("Add character", width="stretch", on_click=adding_character, help=EnterText.character_help)
    subcol2.button("Add alias", width="stretch", on_click=adding_alias, help=EnterText.alias_help)
    element.button(
        ManageCharacters.manage_button,
        width="stretch",
        on_click=managing_characters,
        help=ManageCharacters.manage_help,
    )

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

    # Aliases can only be attached to characters in this book. If none exist
    # yet, avoid rendering an empty form (a Streamlit form must contain a submit
    # button) and prompt the user to add a character first.
    if not st.session_state.get('book_character_dict'):
        element.warning(AliasForm.no_characters)
        element.button('Cancel adding alias', width="stretch", on_click=adding_text)
        return

    st.session_state['current_alias'] = Alias(
        book=st.session_state['current_book'].title
    )
    form_key = f"alias_{st.session_state.get('_alias_form_count', 0)}"
    with element.form(form_key):
        st.session_state['current_alias'].to_form()

    element.button('Cancel adding alias', width="stretch", on_click=adding_text)


def manage_characters_entry(element):

    element.subheader(ManageCharacters.header)
    element.write(ManageCharacters.intro)

    book = st.session_state['current_book']
    # Rebuild the book-scoped character lookup from the book's reference list so
    # it reflects any additions/deletions, and keep it in session state so the
    # alias form stays scoped to this book.
    character_dict = book.get_character_dict()
    st.session_state['book_character_dict'] = character_dict

    if not character_dict:
        element.info(ManageCharacters.no_characters)
    else:
        for name, character_ref in character_dict.items():
            with element.expander(name):
                character_doc = character_ref.get()
                if not character_doc.exists:
                    continue
                aliases = list(
                    st.session_state['firestore'].query_stream(
                        collection='aliases',
                        field='character',
                        op='==',
                        value=character_ref,
                    )
                )
                st.write(ManageCharacters.aliases_label)
                if not aliases:
                    st.caption(ManageCharacters.no_aliases)
                for alias_doc in aliases:
                    alias_data = alias_doc.to_dict()
                    alias_name = alias_data.get('name', '')
                    alias_col, button_col = st.columns([3, 1])
                    alias_col.write(alias_name)
                    if button_col.button(
                        ManageCharacters.delete_alias_button,
                        key=f"delete_alias_{alias_doc.id}",
                    ):
                        confirm_delete_alias(alias_data, alias_name)
                if st.button(
                    ManageCharacters.delete_character_button,
                    key=f"delete_character_{character_ref.id}",
                ):
                    confirm_delete_character(character_doc.to_dict(), name)

    element.button(ManageCharacters.done_button, width="stretch", on_click=adding_text)


def user_entry_box(element, image_height, delta=50):
    if st.session_state.now_entering == 'text':
        text_entry(element, image_height, delta)
    elif st.session_state.now_entering == 'character':
        character_entry(element)
    elif st.session_state.now_entering == 'alias':
        alias_entry(element)
    elif st.session_state.now_entering == 'manage':
        manage_characters_entry(element)

# def create_current_page_from_db():
#     st.session_state.current_page = Page(
#         st.session_state.firestore.get_by_reference(
#             collection='pages',
#             document_ref=f"{st.session_state.current_book.document_id}_{st.session_state.current_page_number}"
#         ).to_dict()
#     )


page_layout(current_page="./pages/enter_text.py")

fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

# Rebuild the page cache whenever the current book changes. Without this, the
# dict for the first book opened persists in session state and is shown for
# every subsequent book (stale text leaking across books).
_book_id = st.session_state.current_book.document_id
if (
    'book_pages_dict' not in st.session_state
    or st.session_state.get('_pages_dict_book_id') != _book_id
):
    create_page_dict_from_db()
    st.session_state['_pages_dict_book_id'] = _book_id
    st.session_state['current_page_number'] = 1
    st.session_state['now_entering'] = 'text'
    st.session_state['_page_text_editing'] = None
    # Build the book-scoped character lookup so alias entry is restricted to
    # characters in this book (rather than every character in the database).
    st.session_state['book_character_dict'] = (
        st.session_state.current_book.get_character_dict()
    )

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


