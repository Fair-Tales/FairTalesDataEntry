"""Photo-initiated book creation (#59).

Entry path where the user starts by uploading the book's page photos. The
designated title-page image is sent to Claude to extract the title, author(s),
illustrator(s), publisher and publication year, which then pre-populate the normal
Add Book form. Extracted author/illustrator/publisher names are fuzzy-matched
against the existing session lookup dicts before falling through to the existing
"create new" sub-flows.

This is an ADDITIONAL entry path — the manual Add Book flow is unchanged. It is the
keystone for the wider AI-assisted data entry work (#103/#84/#63).
"""

import natsort
import streamlit as st
import anthropic

from text_content import BookPhotoEntry
from utilities import (
    page_layout,
    check_authentication_status,
    navigate_to,
    extract_book_metadata,
    fuzzy_match_name,
)

check_authentication_status()
page_layout(current_page="./pages/add_book_photos.py")


def _match_person(extracted_name, lookup_dict_key, extracted_session_key, current_key):
    """Fuzzy-match an extracted author/illustrator name against an existing lookup
    dict. On a match, pre-select the existing record (stored as the matched
    Firestore snapshot under ``current_key``). On no match, stash the raw extracted
    name under ``extracted_session_key`` so the "create new" sub-form is seeded.
    """
    lookup_dict = st.session_state.get(lookup_dict_key, {})
    match = fuzzy_match_name(extracted_name, list(lookup_dict.keys()))
    if match is not None and match in lookup_dict:
        st.session_state[current_key] = lookup_dict[match].get()
    else:
        st.session_state[extracted_session_key] = extracted_name


def _apply_extracted_metadata(metadata):
    """Pre-populate the current Book and session state from extracted metadata."""
    book = st.session_state['current_book']

    # Store the raw Claude response (and the parsed dict) for audit/debugging.
    st.session_state['book_extraction_raw'] = metadata.get('raw')
    st.session_state['book_extraction'] = metadata

    # Clear any leftover entity selections from a previous (cancelled) entry so
    # only freshly matched/extracted values pre-fill the form.
    for _key in (
        'current_author', 'current_illustrator', 'current_publisher',
        'extracted_author_name', 'extracted_illustrator_name',
        'extracted_publisher_name', 'adding_book_entries',
    ):
        st.session_state.pop(_key, None)

    title = metadata.get('title')
    if title:
        # Safe: book is unregistered, so the write-through Field is a no-op here.
        book.title = title

    year = metadata.get('published_year')
    if isinstance(year, int):
        book.published = year

    authors = metadata.get('authors') or []
    if authors:
        _match_person(authors[0], 'author_dict', 'extracted_author_name', 'current_author')

    illustrators = metadata.get('illustrators') or []
    if illustrators:
        _match_person(
            illustrators[0], 'illustrator_dict',
            'extracted_illustrator_name', 'current_illustrator'
        )

    publisher = metadata.get('publisher')
    if publisher:
        publisher_dict = st.session_state.get('publisher_dict', {})
        match = fuzzy_match_name(publisher, list(publisher_dict.keys()))
        if match is not None and match in publisher_dict:
            st.session_state['current_publisher'] = publisher_dict[match].get()
        else:
            st.session_state['extracted_publisher_name'] = publisher


st.header(BookPhotoEntry.header)
st.write(BookPhotoEntry.instructions)

ai_available = 'ANTHROPIC_API_KEY' in st.secrets
if not ai_available:
    st.warning(BookPhotoEntry.no_api_key)

uploaded_files = st.file_uploader(
    BookPhotoEntry.upload_label,
    accept_multiple_files=True,
    key="add_book_photos_uploader",
)

if uploaded_files:
    file_dict = {file.name: file for file in uploaded_files}
    sorted_names = natsort.natsorted(list(file_dict.keys()), reverse=False)

    title_page_name = st.selectbox(
        BookPhotoEntry.title_page_label,
        options=sorted_names,
        index=0,
        help=BookPhotoEntry.title_page_help,
        key="add_book_photos_title_page_select",
    )

    extract_clicked = st.button(
        BookPhotoEntry.extract_button,
        disabled=not ai_available,
        key="add_book_photos_extract_button",
    )

    if extract_clicked:
        # Read all uploaded photos into memory and stash them (in page order) so the
        # later page-upload step can reuse them without a second upload.
        pages = [(name, file_dict[name].getvalue()) for name in sorted_names]
        st.session_state['photo_first_pages'] = pages
        title_bytes = dict(pages)[title_page_name]

        client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
        metadata = None
        try:
            with st.spinner(BookPhotoEntry.extracting):
                metadata = extract_book_metadata(title_bytes, client)
        except anthropic.AnthropicError as exc:
            st.error(BookPhotoEntry.extract_failed.format(error=exc))

        if metadata is not None:
            has_any = bool(
                metadata.get('title')
                or metadata.get('authors')
                or metadata.get('illustrators')
                or metadata.get('publisher')
                or metadata.get('published_year')
            )
            if has_any:
                _apply_extracted_metadata(metadata)
                st.success(BookPhotoEntry.extract_success)
                navigate_to("./pages/add_book.py")
            else:
                # Keep the raw response for audit even when nothing usable parsed.
                st.session_state['book_extraction_raw'] = metadata.get('raw')
                st.session_state['book_extraction'] = metadata
                st.warning(BookPhotoEntry.extract_empty)

cancel_button = st.button(BookPhotoEntry.cancel_text, key="add_book_photos_cancel_button")
if cancel_button:
    st.session_state.pop('photo_first_pages', None)
    st.switch_page("./pages/user_home.py")
