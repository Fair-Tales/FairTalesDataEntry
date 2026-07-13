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

import streamlit as st
import anthropic
from streamlit_option_menu import option_menu

from text_content import BookPhotoEntry, PhotoUpload
from utilities import (
    page_layout,
    check_authentication_status,
    navigate_to,
    extract_photo_first_metadata,
    fuzzy_match_name,
    get_s3_filesystem,
    get_anthropic_client,
    get_ai_settings,
)
from background_pipeline import (
    start_page_processing_job,
    cancel_page_processing_job,
)
from photo_upload import (
    get_upload_session_id,
    generate_put_urls,
    generate_manifest_put_url,
    build_uploader_html,
    fetch_uploaded_photos,
    cleanup_prefix,
    reset_upload_session,
    render_go_to_phone,
    render_photo_instructions,
    render_uploaded_photos_list,
    uploads_settled,
    upload_batch_ready,
)

# Shared "Upload here / Go to phone" chooser styling, reused across the photo
# upload surfaces (#143).
_UPLOAD_MENU_STYLES = {
    "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
    "nav-link-selected": {"background-color": "green"},
}

check_authentication_status()
page_layout(current_page="./pages/add_book_photos.py")

# Direct-to-S3 upload flow key (#118): namespaces this surface's temp prefix
# (uploads/single/{session_id}/) so it never collides with the other migrated
# upload surfaces (pages / batch / collection) within one browser session.
UPLOAD_FLOW_KEY = "single"


def _cleanup_uploads():
    """Drop this session's temp ``uploads/single/{session_id}/`` buffer + session
    id once we are leaving this page (#124).

    Called on the paths that navigate away with the uploaded photos already safely
    held in memory under ``photo_first_pages`` — extraction success, "enter
    manually", and cancel. The downstream reuse pipeline
    (``uploader.upload_widget``) writes those in-memory photos to
    ``sawimages/{title}/`` and never re-reads this S3 buffer, so it is dead weight
    once we leave and would otherwise orphan. Deliberately NOT called on the
    read/retry paths (a failed extraction re-lists this prefix on the next "Read
    the book" click). Scoped strictly to THIS session's prefix; ``cleanup_prefix``
    swallows/logs any S3 failure.
    """
    cleanup_prefix(get_s3_filesystem(), UPLOAD_FLOW_KEY, session_id)
    reset_upload_session(UPLOAD_FLOW_KEY)


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

    # Clear any leftover entity selections / AI-prefill flags from a previous
    # (cancelled) entry so only freshly matched/extracted values pre-fill the form.
    for _key in (
        'current_author', 'current_illustrator', 'current_publisher',
        'extracted_author_name', 'extracted_illustrator_name',
        'extracted_publisher_name', 'adding_book_entries',
        'ai_prefilled_author', 'ai_prefilled_illustrator',
        'ai_prefilled_publisher', 'ai_prefilled_year',
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

    # Flag each field the AI pre-filled so the Add-Book form can show a concise
    # "Found by AI from your photos" caption next to it (#155). The year is the
    # book's first-published year read from the copyright page (#150).
    year = metadata.get('published_year')
    if isinstance(year, int):
        book.published = year
        st.session_state['ai_prefilled_year'] = True

    authors = metadata.get('authors') or []
    if authors:
        _match_person(authors[0], 'author_dict', 'extracted_author_name', 'current_author')
        st.session_state['ai_prefilled_author'] = True

    illustrators = metadata.get('illustrators') or []
    if illustrators:
        _match_person(
            illustrators[0], 'illustrator_dict',
            'extracted_illustrator_name', 'current_illustrator'
        )
        st.session_state['ai_prefilled_illustrator'] = True

    publisher = metadata.get('publisher')
    if publisher:
        publisher_dict = st.session_state.get('publisher_dict', {})
        match = fuzzy_match_name(publisher, list(publisher_dict.keys()))
        if match is not None and match in publisher_dict:
            st.session_state['current_publisher'] = publisher_dict[match].get()
        else:
            st.session_state['extracted_publisher_name'] = publisher
        st.session_state['ai_prefilled_publisher'] = True


st.header(BookPhotoEntry.header)
st.write(BookPhotoEntry.instructions)
# Canonical "how to photograph a book" guidance (#186), shown on every upload surface.
render_photo_instructions(expanded=True)

ai_available = 'ANTHROPIC_API_KEY' in st.secrets
if not ai_available:
    st.warning(BookPhotoEntry.no_api_key)

# Direct browser-to-S3 upload (#114). Mint a stable per-session temp prefix
# (uploads/single/{session_id}/) once, then let the user pick HOW to fill it (#143):
# upload from this device, or scan a QR and upload from a phone. Either way the
# photos land in the SAME prefix, so the single "Read the book" button below reads
# it regardless of which method was used. Each photo PUTs straight to S3, bypassing
# the Streamlit websocket that drops on mobile while the native picker is open.
session_id = get_upload_session_id(UPLOAD_FLOW_KEY)

upload_method = option_menu(
    None,
    [PhotoUpload.method_upload_here, PhotoUpload.method_go_to_phone],
    default_index=0,
    icons=['laptop', 'phone'],
    menu_icon="cast",
    orientation="horizontal",
    key="add_book_photos_upload_menu",
    styles=_UPLOAD_MENU_STYLES,
)

if upload_method == PhotoUpload.method_go_to_phone:
    render_go_to_phone(UPLOAD_FLOW_KEY, session_id)
else:
    st.write(BookPhotoEntry.direct_upload_instructions)
    put_urls = generate_put_urls(UPLOAD_FLOW_KEY, session_id)
    manifest_url = generate_manifest_put_url(UPLOAD_FLOW_KEY, session_id)
    # st.iframe (Streamlit 1.56+) replaces the deprecated st.components.v1.html. An
    # HTML string is embedded via srcdoc exactly as before (same-origin preserved,
    # so the browser→S3 PUT still uses the app origin that the S3 CORS policy allows).
    st.iframe(build_uploader_html(put_urls, manifest_url), height=460)

def _run_extraction(fs, *, check_settled):
    """Fetch the uploaded photos, run the two-pass extraction, and either route to
    the pre-filled Add-Book form (#155) or surface the "enter manually" state.

    ``check_settled`` gates on ``uploads_settled`` for the manual "Go" button; the
    automatic path skips it because the poll fragment has already confirmed the
    upload count is stable (#142).
    """
    if check_settled:
        # The uploader iframe is one-way, so gate reading on the upload being
        # COMPLETE — primarily the explicit manifest (#199), with the legacy
        # count-stability heuristic only as a manifest-less fallback — otherwise
        # reading mid-upload reads a PARTIAL batch (#142) and bakes a shifted
        # page order into the book. See photo_upload.uploads_settled.
        with st.spinner(BookPhotoEntry.checking_uploads):
            settled, uploaded_keys = uploads_settled(fs, UPLOAD_FLOW_KEY, session_id)
        if uploaded_keys and not settled:
            # Not ready: block, but never dead-end (#199) — the persistent
            # prompt rendered below the Go button offers an explicit
            # proceed-anyway with the photos already uploaded.
            st.session_state['_upload_incomplete_count'] = len(uploaded_keys)
            st.warning(BookPhotoEntry.uploads_in_progress)
            return
        st.session_state.pop('_upload_incomplete_count', None)

    with st.spinner(BookPhotoEntry.reading_photos):
        # List the temp prefix and pull the uploaded photos (in page order) into
        # memory. See photo_upload.fetch_uploaded_photos for the memory tradeoff.
        pages = fetch_uploaded_photos(fs, UPLOAD_FLOW_KEY, session_id)

    if not pages:
        st.warning(BookPhotoEntry.no_photos_uploaded)
        # Drop the "ready" latch so the auto-poll can re-detect a later upload.
        st.session_state.pop('photos_ready_auto', None)
        return

    # Reuse the existing in-memory pipeline: stash the downloaded photos so the
    # downstream page-upload step (uploader.upload_widget) orientation-corrects,
    # crops, OCRs and writes them to sawimages/{title}/ exactly as today, then
    # cleans up this temp prefix.
    st.session_state['photo_first_pages'] = pages

    client = get_anthropic_client()

    metadata = None
    try:
        with st.spinner(BookPhotoEntry.extracting):
            # Auto-detect the title page (and the copyright page) via the Haiku
            # locate pass — consistent with the batch flow; no manual selection.
            # If detection is wrong, the user corrects the details on the form.
            metadata = extract_photo_first_metadata(pages, client)
    except anthropic.AnthropicError as exc:
        st.error(BookPhotoEntry.extract_failed.format(error=exc))
        st.session_state.pop('photos_ready_auto', None)
        return

    if metadata is None:
        st.session_state.pop('photos_ready_auto', None)
        return

    # Kick the slow per-page crop/rotation/OCR (+ character-detection) pipeline
    # off NOW, as a DURABLE background job (#179), so it runs while the user
    # checks the metadata below and fills in the Add-Book form. The worker writes
    # each corrected page to S3 and each result to Firestore as it goes, so the
    # work survives a websocket drop / reconnect / app restart and costs no extra
    # RAM; ``uploader._process_photo_batch`` finalises whatever is done instead of
    # redoing it. Started AFTER metadata extraction so the S3 working folder can
    # be seeded with the extracted title (no rename needed if the user keeps it).
    # ``fs``/``db`` are built on THIS (script) thread and handed to the worker.
    if client is not None:
        start_page_processing_job(
            st.session_state,
            get_s3_filesystem(),
            st.session_state.firestore.connect_book(),
            client,
            get_ai_settings(),
            [data for _name, data in pages],
            entered_by=st.session_state.get('username'),
            extracted_title=metadata.get('title'),
        )

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
        # Photos are safely in photo_first_pages (memory) and the reuse pipeline
        # writes them to sawimages/ from there, never re-reading this S3 buffer —
        # so clear it now we are leaving the page (#124).
        _cleanup_uploads()
        navigate_to("./pages/add_book.py")
    else:
        # Nothing usable parsed. Keep the raw response + a diagnostic and flag the
        # empty state so the "enter manually" UI below survives the rerun (the
        # photos stay in photo_first_pages). Drop the auto latch so the poll does
        # not re-trigger the same empty read every few seconds.
        st.session_state['book_extraction_raw'] = metadata.get('raw')
        st.session_state['book_extraction'] = metadata
        st.session_state['photo_extract_empty'] = True
        st.session_state['photo_extract_diag'] = {
            'pages': len(pages),
            'located': metadata.get('located'),
            'raw': metadata.get('raw'),
        }
        st.session_state.pop('photos_ready_auto', None)


# --- Automatic upload-completion detection + auto-run (#155) -------------------
# The direct-to-S3 uploader iframe is intentionally ONE-WAY: it cannot post a value
# back to Streamlit. st.iframe / st.components.v1.html only report their frame
# height (streamlit:setFrameHeight); a genuinely reactive "uploads finished" signal
# needs a DECLARED bidirectional custom component (Streamlit.setComponentValue),
# which is out of scope for this release. So completion is detected with a BOUNDED
# auto-refresh POLL: an st.fragment reruns every few seconds and lists the temp
# prefix; because the browser PUTs the selected photos sequentially, a still-running
# upload shows as a growing key count, so a non-empty count that is unchanged across
# two consecutive polls means the batch has finished. We then flag it and trigger a
# full rerun that reads the book automatically — no click (#142 follow-up).
# TRADE-OFF (flagged): the poll lists S3 every POLL_INTERVAL_SECONDS while the user
# is on this page (cheap: one list per tick), and completion is INFERRED — a long
# stall between two photos could read early, though the sequential PUTs + two-sample
# gate make that unlikely. A fully reactive gate would need the declared component
# above. The manual "Go" button below remains as a fallback.
POLL_INTERVAL_SECONDS = 3
MAX_IDLE_POLLS = 200  # ~10 min ceiling on idle polling before we defer to "Go".
# Polls with photos present but the manifest still absent/mismatched and the
# count unchanged before we surface the "upload looks stalled" warning (#199):
# ~1 minute — long enough for a slow last photo, short enough not to strand
# the user staring at a silent page.
STALL_POLLS = 20


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def _auto_upload_watcher(flow_key, sid):
    # Stop once we've decided to read, hit the empty state, or (no API key) can't
    # extract anyway. Returning early keeps the fragment cheap on later ticks.
    if (
        not ai_available
        or st.session_state.get('photos_ready_auto')
        or st.session_state.get('photo_extract_empty')
    ):
        return
    fs = get_s3_filesystem()
    # The AUTO read requires the explicit completion manifest to match the keys
    # present (#199) — completion is no longer inferred from a stable count,
    # which fired early on a stalled concurrent batch and read a partial book.
    ready, keys, _manifest = upload_batch_ready(fs, flow_key, sid)
    prev = st.session_state.get('_auto_last_count', 0)
    st.session_state['_auto_last_count'] = len(keys)
    if keys:
        # Photos are arriving: reset the idle ceiling and show live progress.
        st.session_state['_auto_polls'] = 0
        st.caption(BookPhotoEntry.auto_upload_progress.format(n=len(keys)))
        # Live "uploaded so far" list + duplicate/gap guards (#186/#199) so the
        # user can verify order/completeness and catch a photo uploaded twice.
        render_uploaded_photos_list(fs, flow_key, sid)
        if ready:
            st.session_state.pop('_auto_stall_polls', None)
            st.session_state['photos_ready_auto'] = True
            st.rerun()  # full-app rerun so the extraction below runs automatically
        elif len(keys) == prev:
            # Photos present but completion unconfirmed and nothing new landing:
            # after ~a minute tell the user it looks stalled and point at the
            # retry / manual-proceed affordances (no dead-end, #199).
            stall_polls = st.session_state.get('_auto_stall_polls', 0) + 1
            st.session_state['_auto_stall_polls'] = stall_polls
            if stall_polls >= STALL_POLLS:
                st.warning(BookPhotoEntry.upload_stalled_warning)
            else:
                st.caption(BookPhotoEntry.auto_upload_waiting_finish)
        else:
            st.session_state['_auto_stall_polls'] = 0
            st.caption(BookPhotoEntry.auto_upload_waiting_finish)
    else:
        polls = st.session_state.get('_auto_polls', 0) + 1
        st.session_state['_auto_polls'] = polls
        if polls >= MAX_IDLE_POLLS:
            st.caption(BookPhotoEntry.auto_upload_timeout)
        else:
            st.caption(BookPhotoEntry.auto_upload_waiting)


# Watch for the upload finishing and auto-run (works for both the upload-here and
# go-to-phone methods, which land in the same prefix). Skipped without an API key.
if ai_available:
    _auto_upload_watcher(UPLOAD_FLOW_KEY, session_id)

# Once the poll flags the upload complete, read the book automatically — no click.
if st.session_state.get('photos_ready_auto') and not st.session_state.get('photo_extract_empty'):
    st.info(BookPhotoEntry.auto_reading)
    _run_extraction(get_s3_filesystem(), check_settled=False)

# Manual fallback trigger, kept only for the rare case the automatic read does not
# begin (e.g. the poll ceiling was reached). Normally the user never needs it.
read_clicked = st.button(
    BookPhotoEntry.read_button,
    disabled=not ai_available,
    help=BookPhotoEntry.manual_read_help,
    key="add_book_photos_read_button",
)
if read_clicked:
    _run_extraction(get_s3_filesystem(), check_settled=True)

# No-dead-end escape hatch (#199): a manual read that found photos but no
# completion confirmation blocks above and sets _upload_incomplete_count.
# Rendered OUTSIDE the read-click block so the proceed-anyway button survives
# the rerun; the user can also simply wait / re-select photos and click Go
# again (retry).
_incomplete_count = st.session_state.get('_upload_incomplete_count')
if _incomplete_count and not st.session_state.get('photos_ready_auto'):
    st.warning(BookPhotoEntry.upload_incomplete_prompt.format(n=_incomplete_count))
    if st.button(
        BookPhotoEntry.force_read_button,
        disabled=not ai_available,
        key="add_book_photos_force_read_button",
    ):
        st.session_state.pop('_upload_incomplete_count', None)
        _run_extraction(get_s3_filesystem(), check_settled=False)

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
            'ai_prefilled_author', 'ai_prefilled_illustrator',
            'ai_prefilled_publisher', 'ai_prefilled_year',
            'photos_ready_auto', '_auto_last_count', '_auto_polls',
            '_auto_stall_polls', '_upload_incomplete_count',
        ):
            st.session_state.pop(_key, None)
        # Photos stay in photo_first_pages (memory) for the reuse pipeline, so the
        # S3 buffer is no longer needed — clear it as we leave the page (#124).
        _cleanup_uploads()
        navigate_to("./pages/add_book.py")

cancel_button = st.button(BookPhotoEntry.cancel_text, key="add_book_photos_cancel_button")
if cancel_button:
    # Stop the background processing worker (if one was started for these
    # photos) so a cancelled entry does not keep burning AI calls, and mark the
    # durable job cancelled so any resumed worker stops too (#179).
    cancel_page_processing_job(
        st.session_state,
        st.session_state.firestore.connect_book() if 'firestore' in st.session_state else None,
    )
    # Remove any photos already uploaded to the temp prefix so they don't orphan,
    # and drop the session id so the next entry mints a fresh prefix.
    _cleanup_uploads()
    for _key in (
        'photo_first_pages', 'photo_extract_empty', 'photo_extract_diag',
        'photos_ready_auto', '_auto_last_count', '_auto_polls',
        '_auto_stall_polls', '_upload_incomplete_count',
        'ai_prefilled_author', 'ai_prefilled_illustrator',
        'ai_prefilled_publisher', 'ai_prefilled_year',
    ):
        st.session_state.pop(_key, None)
    st.switch_page("./pages/user_home.py")
