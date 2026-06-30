import json
from datetime import datetime
import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions, AIPrompts, BookForm, BookEditHome
from utilities import (
    check_authentication_status, page_layout, confirm_submit, get_anthropic_client,
)

check_authentication_status()

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


def manage_characters():
    # Character management lives in the text-entry page's "manage" view. Open
    # enter_text.py directly there via the _open_manage_characters flag, which
    # enter_text applies after its own per-book state init (issue #106). Like
    # enter_text(), this requires uploaded photos because enter_text.py always
    # renders the page image.
    if st.session_state.current_book.photos_uploaded:
        st.session_state['current_page_number'] = 1
        st.session_state['_open_manage_characters'] = True
        st.switch_page("./pages/enter_text.py")
    else:
        st.warning(Alerts.please_uploaded_photos)


def suggest_themes():
    client = get_anthropic_client()
    if client is None:
        st.warning(BookEditHome.no_api_key)
        return

    book_id = st.session_state.current_book.document_id
    page_count = st.session_state.current_book.page_count
    story_text_parts = []

    for page_num in range(1, page_count + 1):
        doc = st.session_state.firestore.get_by_reference(
            collection='pages',
            document_ref=f"{book_id}_{page_num}"
        )
        if doc.exists:
            data = doc.to_dict()
            if data.get('contains_story') and data.get('text', '').strip():
                story_text_parts.append(data['text'].strip())

    if not story_text_parts:
        st.warning(BookEditHome.no_story_text)
        return

    full_text = "\n\n".join(story_text_parts)
    prompt = AIPrompts.theme_detection + full_text

    with st.spinner(BookEditHome.analysing_spinner):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
        except Exception as e:
            st.error(BookEditHome.detection_failed.format(error=e))
            return

    theme_keys = list(BookForm.theme_options.keys())
    added = []
    for key in theme_keys:
        if result.get(key) and not getattr(st.session_state.current_book, key):
            setattr(st.session_state.current_book, key, True)
            added.append(BookForm.theme_options[key])

    if added:
        st.success(BookEditHome.themes_suggested.format(themes=', '.join(added), reasoning=result.get('reasoning', '')))
    else:
        st.info(BookEditHome.no_new_themes.format(reasoning=result.get('reasoning', '')))


page_layout()

st.title(BookEditHome.editing_book_title.format(title=st.session_state.current_book.title))

# Reassure the user that their edits are persisted (issue #53). last_updated is
# only a datetime for a book that has been saved; a brand-new book leaves the
# default sentinel (-1), which we skip.
if isinstance(st.session_state.current_book.last_updated, datetime):
    st.caption(Instructions.last_saved(st.session_state.current_book.last_updated))

check_authentication_status()

edit_option = option_menu(
    None, [BookEditHome.menu_instructions, BookEditHome.menu_edit_metadata, BookEditHome.menu_upload_photos, BookEditHome.menu_enter_text, BookEditHome.menu_manage_characters],
    default_index=0,
    icons=['info-circle', 'list-stars', 'image', 'pencil-square', 'people'],
    menu_icon="cast", orientation="horizontal",
    key="book_edit_option_menu",
    styles={
        "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
        "nav-link-selected": {"background-color": "green"},
    }
)

edit_navigation_dict = {
    BookEditHome.menu_instructions: instructions,
    BookEditHome.menu_edit_metadata: edit_book_details,
    BookEditHome.menu_upload_photos: add_photos,
    BookEditHome.menu_enter_text: enter_text,
    BookEditHome.menu_manage_characters: manage_characters
}

edit_navigation_dict[edit_option]()

if (
    st.session_state.current_book.page_count > 0
    and st.session_state.current_book.photos_uploaded
):
    if st.button(BookEditHome.suggest_themes_button, key="book_edit_suggest_themes_button"):
        suggest_themes()

if st.button(BookEditHome.back_to_home_button, key="book_edit_back_home_button"):
    st.switch_page("./pages/user_home.py")

if st.button(BookEditHome.finish_submit_button, key="book_edit_finish_submit_button"):
    confirm_submit()


