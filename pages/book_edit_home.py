import json
import anthropic
import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions, AIPrompts, BookForm
from utilities import check_authentication_status, page_layout, confirm_submit, check_authentication_status

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


def suggest_themes():
    if 'ANTHROPIC_API_KEY' not in st.secrets:
        st.warning("AI theme suggestion requires an Anthropic API key.")
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
        st.warning("No story text found. Please enter text for the book pages first.")
        return

    full_text = "\n\n".join(story_text_parts)
    prompt = AIPrompts.theme_detection + full_text

    with st.spinner("Analysing book text for themes..."):
        try:
            client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
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
            st.error(f"Theme detection failed: {e}")
            return

    theme_keys = list(BookForm.theme_options.keys())
    added = []
    for key in theme_keys:
        if result.get(key) and not getattr(st.session_state.current_book, key):
            setattr(st.session_state.current_book, key, True)
            added.append(BookForm.theme_options[key])

    if added:
        st.success(f"Themes suggested: {', '.join(added)}. Reasoning: {result.get('reasoning', '')}")
    else:
        st.info(f"No new themes to add. Reasoning: {result.get('reasoning', '')}")


page_layout()

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

edit_navigation_dict = {
    "Instructions": instructions,
    "Edit metadata": edit_book_details,
    "Upload photos": add_photos,
    "Enter text": enter_text
}

edit_navigation_dict[edit_option]()

if (
    st.session_state.current_book.page_count > 0
    and st.session_state.current_book.photos_uploaded
):
    if st.button("🏷 Suggest themes"):
        suggest_themes()

if st.button("Back to home menu."):
    st.switch_page("./pages/user_home.py")

if st.button("Finish and submit book"):
    confirm_submit()


