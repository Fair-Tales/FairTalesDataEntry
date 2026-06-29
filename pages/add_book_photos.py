"""Photo-initiated book creation (#59) with two-pass metadata extraction (#109).

Entry path where the user starts by uploading the book's page photos. A cheap
Claude Haiku "locate" pass scans ALL page images to find the title page and the
copyright/imprint page (whose position varies); a targeted Claude Sonnet "extract"
pass then reads only those one or two pages — the title page for title/author(s)/
illustrator(s) and the copyright page for publisher/first-published year/ISBN — and
feeds any ISBN into the Google Books lookup (the most reliable source). The merged
result pre-populates the normal Add Book form (including the ``isbn_metadata``
pre-fill). Extracted author/illustrator/publisher names are fuzzy-matched against
the existing session lookup dicts before falling through to the existing "create
new" sub-flows.

This is an ADDITIONAL entry path — the manual Add Book flow is unchanged. It makes
the #63 ISBN/copyright-page machinery reachable (#103) for AI-assisted data entry.
"""

import natsort
import streamlit as st
import anthropic

from text_content import BookPhotoEntry
from utilities import (
    page_layout,
    check_authentication_status,
    navigate_to,
    extract_photo_first_metadata,
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

    # ISBN → Google Books metadata pre-fills the Add-Book form (#103/#63). Set it
    # (or clear a previous book's lookup) so the form's isbn_metadata fallback is
    # always in step with this extraction.
    isbn_metadata = metadata.get('isbn_metadata')
    if isbn_metadata:
        st.session_state['isbn_metadata'] = isbn_metadata
    else:
        st.session_state.pop('isbn_metadata', None)

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

        client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
        metadata = None
        try:
            with st.spinner(BookPhotoEntry.extracting):
                # Auto-detect the title page (and the copyright page) via the Haiku
                # locate pass — consistent with the batch flow; no manual selection.
                # If detection is wrong, the user corrects the details on the form.
                metadata = extract_photo_first_metadata(pages, client)
        except anthropic.AnthropicError as exc:
            st.error(BookPhotoEntry.extract_failed.format(error=exc))

        if metadata is not None:
            has_any = bool(
                metadata.get('title')
                or metadata.get('authors')
                or metadata.get('illustrators')
                or metadata.get('publisher')
                or metadata.get('published_year')
                or metadata.get('isbn_metadata')
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
