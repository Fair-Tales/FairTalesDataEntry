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

import s3fs
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
from photo_upload import (
    get_upload_session_id,
    generate_put_urls,
    build_uploader_html,
    fetch_uploaded_photos,
    cleanup_prefix,
)

check_authentication_status()
page_layout(current_page="./pages/add_book_photos.py")

# Direct-to-S3 upload flow key (#118): namespaces this surface's temp prefix
# (uploads/single/{session_id}/) so it never collides with the other migrated
# upload surfaces (pages / batch / collection) within one browser session.
UPLOAD_FLOW_KEY = "single"


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


def _filesystem():
    """Build an authenticated s3fs filesystem from the AWS secrets (same config
    as the rest of the app)."""
    return s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY'],
    )


st.header(BookPhotoEntry.header)
st.write(BookPhotoEntry.instructions)

ai_available = 'ANTHROPIC_API_KEY' in st.secrets
if not ai_available:
    st.warning(BookPhotoEntry.no_api_key)

st.write(BookPhotoEntry.direct_upload_instructions)

# Direct browser-to-S3 upload (#114). Mint a stable per-session temp prefix
# (uploads/{session_id}/) and a batch of presigned PUT URLs, then render the
# browser-side uploader. Each photo PUTs straight from the phone to S3, bypassing
# the Streamlit websocket that drops on mobile while the native photo picker is
# full-screen. The component is intentionally one-way: we discover what landed by
# listing the S3 prefix when the user clicks "Read the book".
session_id = get_upload_session_id(UPLOAD_FLOW_KEY)
put_urls = generate_put_urls(UPLOAD_FLOW_KEY, session_id)
# st.iframe (Streamlit 1.56+) replaces the deprecated st.components.v1.html. An HTML
# string is embedded via srcdoc exactly as before (same-origin preserved, so the
# browser→S3 PUT still uses the app origin that the S3 CORS policy allows).
st.iframe(build_uploader_html(put_urls), height=460)

read_clicked = st.button(
    BookPhotoEntry.read_button,
    disabled=not ai_available,
    key="add_book_photos_read_button",
)

if read_clicked:
    with st.spinner(BookPhotoEntry.reading_photos):
        # List the temp prefix and pull the uploaded photos (in page order) into
        # memory. See photo_upload.fetch_uploaded_photos for the memory tradeoff.
        pages = fetch_uploaded_photos(_filesystem(), UPLOAD_FLOW_KEY, session_id)

    if not pages:
        st.warning(BookPhotoEntry.no_photos_uploaded)
    else:
        # Reuse the existing in-memory pipeline: stash the downloaded photos so the
        # downstream page-upload step (uploader.upload_widget) orientation-corrects,
        # crops, OCRs and writes them to sawimages/{title}/ exactly as today, then
        # cleans up this temp prefix.
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
                st.session_state.pop('photo_extract_empty', None)
                _apply_extracted_metadata(metadata)
                st.success(BookPhotoEntry.extract_success)
                navigate_to("./pages/add_book.py")
            else:
                # Nothing usable parsed. Keep the raw response + a diagnostic and
                # flag the empty state so the "enter manually" UI below survives the
                # rerun (the photos stay in photo_first_pages).
                st.session_state['book_extraction_raw'] = metadata.get('raw')
                st.session_state['book_extraction'] = metadata
                st.session_state['photo_extract_empty'] = True
                st.session_state['photo_extract_diag'] = {
                    'pages': len(pages),
                    'located': metadata.get('located'),
                    'raw': metadata.get('raw'),
                }

# Persistent "couldn't extract — enter manually" state, rendered OUTSIDE the
# read-click block so the proceed button survives the rerun the click triggers.
if st.session_state.get('photo_extract_empty'):
    st.warning(BookPhotoEntry.extract_empty)
    _diag = st.session_state.get('photo_extract_diag') or {}
    with st.expander(BookPhotoEntry.extract_diag_header):
        st.write(BookPhotoEntry.extract_diag_pages.format(n=_diag.get('pages', 0)))
        st.write(BookPhotoEntry.extract_diag_located.format(located=_diag.get('located')))
        st.write(BookPhotoEntry.extract_diag_raw)
        st.code(str(_diag.get('raw') or "")[:2000])
    if st.button(
        BookPhotoEntry.enter_manually_button, key="add_book_photos_manual_button"
    ):
        # Start the Add Book form blank (clear any leftover extracted selections),
        # keep the uploaded photos so they're processed/stored as usual, and go.
        for _key in (
            'current_author', 'current_illustrator', 'current_publisher',
            'extracted_author_name', 'extracted_illustrator_name',
            'extracted_publisher_name', 'isbn_metadata', 'adding_book_entries',
            'photo_extract_empty', 'photo_extract_diag',
        ):
            st.session_state.pop(_key, None)
        navigate_to("./pages/add_book.py")

cancel_button = st.button(BookPhotoEntry.cancel_text, key="add_book_photos_cancel_button")
if cancel_button:
    # Remove any photos already uploaded to the temp prefix so they don't orphan.
    cleanup_prefix(_filesystem(), UPLOAD_FLOW_KEY, session_id)
    for _key in ('photo_first_pages', 'photo_extract_empty', 'photo_extract_diag'):
        st.session_state.pop(_key, None)
    st.switch_page("./pages/user_home.py")
