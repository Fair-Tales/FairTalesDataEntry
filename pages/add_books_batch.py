"""Batch multi-book photo upload (#84): split one batch of photos covering
several books into separate book records (bulk create).

Builds on the photo-first single-book flow (#59) and its two-pass metadata
extraction (#109). The user uploads ONE batch of photos that spans MULTIPLE
books, taken in sequence. A two-stage splitter groups the photos by book:

  Stage 1 (primary): a cheap OpenCV/PIL detector flags FULLY BLACK separator
    frames — the user covers the lens and takes a black photo between books.
    Each black frame is a book boundary and is DISCARDED. (``image_processing.
    is_black_frame`` / ``utilities.split_photo_batch``.)
  Stage 2 (fallback): when no black separators are present, a Claude Haiku pass
    detects each book's cover/title page and splits there
    (``utilities.locate_cover_pages``).

Each grouped set is then fed through the existing single-book photo-first
machinery: metadata extraction (``extract_photo_first_metadata``) reads each
book's title/creators/year, and the per-page processing pipeline
(``pages.uploader._process_page`` + ``extract_page_info``) uploads, corrects and
text-extracts every page — creating a SEPARATE ``Book`` record per group.

A REVIEW step lists the detected books (count + extracted titles) so the user
can confirm the split before anything is committed. Because this is a bulk
create, author / illustrator / publisher are only fuzzy-matched against existing
records (no interactive "create new" sub-forms); unmatched creators are left for
the user to fill in later via the normal Edit-my-books flow.
"""

import streamlit as st
import anthropic
from streamlit_option_menu import option_menu

from data_structures import Book, Page, ExtractionErrorLog, log_extraction_error
from image_processing import exif_transpose_bytes
from pages.uploader import (
    _process_page, extract_page_info, _make_reporter, PageExtractionError,
    _register_placeholder_page,
)
from text_content import BatchBookEntry, Uploader, PhotoUpload
from utilities import (
    page_layout,
    check_authentication_status,
    split_photo_batch,
    extract_photo_first_metadata,
    fuzzy_match_name,
    load_book_dict,
    get_s3_filesystem,
    get_anthropic_client,
    get_ai_settings,
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
)

# Shared "Upload here / Go to phone" chooser styling (#143).
_UPLOAD_MENU_STYLES = {
    "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
    "nav-link-selected": {"background-color": "green"},
}

# Direct-to-S3 upload flow key (#118): namespaces the batch's temp prefix
# (uploads/batch/{session_id}/) so it never collides with the other migrated
# surfaces (single / pages / collection) within one browser session.
UPLOAD_FLOW_KEY = "batch"

check_authentication_status()
page_layout(current_page="./pages/add_books_batch.py")


_BATCH_STATE_KEYS = ('batch_step', 'batch_method', 'batch_detected', 'batch_results')


def _reset_batch_state():
    """Drop all batch-flow session state (large photo payloads included) and the
    direct-to-S3 upload session so a new batch mints a fresh temp prefix."""
    for key in _BATCH_STATE_KEYS:
        st.session_state.pop(key, None)
    reset_upload_session(UPLOAD_FLOW_KEY)


def _doc_id(title):
    """Mirror ``Book.document_id`` so collisions can be checked before a Book is
    created from an extracted title."""
    return title.lower().replace(" ", "_")


def _detect_books(pages, client):
    """Split the batch and read each detected book's metadata.

    Returns (method, detected) where ``method`` is the split method string and
    ``detected`` is a list of ``{'pages': [(name, bytes)], 'metadata': dict}``.
    """
    split = split_photo_batch(pages, client)
    groups = split['groups']

    detected = []
    progress = st.progress(0.0)
    status = st.empty()
    total = len(groups)
    for index, group in enumerate(groups):
        status.write(BatchBookEntry.reading_book.format(n=index + 1, total=total))
        metadata = {}
        metadata_error = None
        if client is not None:
            try:
                metadata = extract_photo_first_metadata(group, client) or {}
            except anthropic.AnthropicError as exc:
                # Reading one book failed — record the error and keep the group
                # (titled with a fallback) so it is still created AND visibly
                # flagged for manual editing, rather than dropped or silently
                # saved as "Untitled book N".
                metadata = {}
                metadata_error = str(exc)
        detected.append({
            'pages': group,
            'metadata': metadata,
            'metadata_error': metadata_error,
        })
        progress.progress((index + 1) / max(total, 1))
    status.empty()
    progress.empty()
    return split['method'], detected


def _match_existing(book, field, names, dict_key):
    """Fuzzy-match the first of ``names`` against an existing lookup dict and, on
    a hit, set the Book's reference field (the Field descriptor resolves the
    matched name string to a Firestore reference). No-op on no match — bulk
    create never creates new people/publishers."""
    if not names:
        return
    options = list(st.session_state.get(dict_key, {}).keys())
    match = fuzzy_match_name(names[0], options)
    if match:
        setattr(book, field, match)


def _make_book_from_metadata(metadata, used_ids):
    """Build an unregistered ``Book`` from extracted metadata, guaranteeing a
    unique, non-colliding title/document id within this batch and the database."""
    firestore = st.session_state['firestore']
    raw_title = (metadata.get('title') or "").strip()
    base_title = raw_title or BatchBookEntry.untitled_title.format(n=len(used_ids) + 1)

    title = base_title
    suffix = 2
    while _doc_id(title) in used_ids or firestore.document_exists('books', _doc_id(title)):
        title = f"{base_title} ({suffix})"
        suffix += 1

    book = Book()
    book.title = title

    year = metadata.get('published_year')
    if isinstance(year, int):
        book.published = year

    _match_existing(book, 'author', metadata.get('authors') or [], 'author_dict')
    _match_existing(book, 'illustrator', metadata.get('illustrators') or [], 'illustrator_dict')
    publisher = metadata.get('publisher')
    _match_existing(book, 'publisher', [publisher] if publisher else [], 'publisher_dict')
    return book


def _process_group_pages(book, group_pages, fs, ai_client, status):
    """Upload, correct and text-extract every page of one book group, mirroring
    the single-book pipeline (``uploader._process_photo_batch``) but scoped to an
    explicit, already-registered ``book``.

    Returns the list of page numbers whose AI text-extraction failed (#132); those
    pages stay in the sequence as blanks for later manual entry."""
    photos_url = f"sawimages/{book.title}"
    total = len(group_pages)
    failed_pages = []
    # Per-book prefix so the shared sub-step messages name the current book (#110).
    prefix = BatchBookEntry.page_prefix.format(title=book.title)

    # Phase 1 — orientation-normalise and upload the raw photos.
    corrected = []
    for index, (_name, raw_bytes) in enumerate(group_pages):
        status.update(label=prefix + Uploader.saving_photo.format(
            current=index + 1, total=total
        ))
        raw_bytes = exif_transpose_bytes(raw_bytes)
        corrected.append(raw_bytes)
        with fs.open(f"{photos_url}/page_{index + 1}.jpg", 'wb') as handle:
            handle.write(raw_bytes)

    book.photos_uploaded = True
    book.photos_url = photos_url
    book.page_count = total

    # Phase 2 — per-page correction + text extraction (when an AI client exists).
    # Report at every sub-step (correct → check → extract) so the websocket stays
    # alive across a long multi-book batch (#110).
    if ai_client is not None:
        for index, raw_bytes in enumerate(corrected):
            page_number = index + 1
            report = _make_reporter(status, page_number, total, prefix)

            # Set fields on the unregistered Page, then register() once (audit
            # item 8 — one write instead of register()+per-field write-throughs).
            page = Page(page_number=page_number, book=book.title)

            # --- Per-page isolation boundary (mirrors pages.uploader.
            # _process_photo_batch, harden-page-loop-error-logging / #171) ---
            #
            # Everything below — _process_page's OpenCV/PIL correction + S3
            # write, the extraction call, and the register() write(s) —
            # previously ran unguarded here too: a single bad photo in ANY
            # book of this multi-book batch (a corrupt frame OpenCV/PIL can't
            # decode, a transient S3 write failure, a Firestore register()
            # error) would abort not just the rest of that book's pages but
            # every LATER book in the batch as well.
            #
            # The broad ``except Exception`` below is the documented exception
            # to the narrow-except rule (see CLAUDE.md): its whole job is to
            # be an isolation boundary around ONE page so a single failure can
            # never blank out the rest of this book (or batch). It is NOT a
            # silent swallow — every failure is (a) logged to the
            # ``extraction_errors`` Firestore collection via the shared
            # ``log_extraction_error`` helper (#129), (b) recorded in
            # ``failed_pages`` so the user gets a "N pages couldn't be
            # processed" warning, and (c) the page is still registered blank
            # (best-effort, via the shared ``_register_placeholder_page``
            # helper) so page numbering for every later page stays correct.
            # The narrower ``except PageExtractionError`` nested inside it is
            # the pre-existing (#132) extraction-failure path, whose detail is
            # already logged by ``extract_page_info`` itself.
            try:
                bytes_for_extraction, _method, _rotation_uncertain = _process_page(
                    raw_bytes, page_number, photos_url, fs, ai_client, report
                )

                report(Uploader.substep_extracting)
                try:
                    text, is_story, _page_type = extract_page_info(
                        bytes_for_extraction, ai_client,
                        book=book, page_number=page_number,
                        page_name=f"page_{page_number}.jpg",
                        flow=ExtractionErrorLog.FLOW_BATCH,
                    )
                except PageExtractionError:
                    # Detail already logged to extraction_errors; keep the
                    # blank page and record it for the user (#132).
                    page.register()
                    failed_pages.append(page_number)
                else:
                    if text:
                        page.text = text
                    page.contains_story = is_story
                    page.register()
            except Exception as exc:  # noqa: BLE001 - isolation boundary, see comment above
                log_extraction_error(
                    book=book,
                    page_number=page_number,
                    page_name=f"page_{page_number}.jpg",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    flow=ExtractionErrorLog.FLOW_BATCH,
                    model=get_ai_settings()['extraction_model'],
                )
                _register_placeholder_page(page, page_number)
                failed_pages.append(page_number)
    else:
        # No API key — register pages without extraction. Guarded the same way
        # (harden-page-loop-error-logging / #171): a single Firestore
        # register() failure here must not abort the rest of this (much
        # simpler) batch either.
        for index in range(total):
            page_number = index + 1
            page = Page(page_number=page_number, book=book.title)
            try:
                page.register()
            except Exception as exc:  # noqa: BLE001 - isolation boundary, see above
                log_extraction_error(
                    book=book,
                    page_number=page_number,
                    page_name=f"page_{page_number}.jpg",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    flow=ExtractionErrorLog.FLOW_BATCH,
                )
                failed_pages.append(page_number)

    return failed_pages


def _create_books(detected, fs, ai_client):
    """Create a separate registered ``Book`` per detected group and process its
    pages. Returns a list of ``{'title', 'pages'}`` summaries."""
    results = []
    used_ids = set()
    total = len(detected)

    # A single live st.status, updated at every per-page sub-step inside
    # _process_group_pages, keeps the browser fed with frequent messages so the
    # websocket survives a long multi-book batch instead of hanging (#110).
    with st.status(BatchBookEntry.creating, expanded=True) as status:
        overall = st.progress(0.0)
        for index, entry in enumerate(detected):
            book = _make_book_from_metadata(entry['metadata'], used_ids)
            used_ids.add(book.document_id)
            book.register()
            # Register into the session lookup so Page.book string resolution
            # works, then invalidate the shared cache for other/new sessions.
            st.session_state.setdefault('book_dict', {})[book.title] = book.get_ref()
            failed_pages = _process_group_pages(
                book, entry['pages'], fs, ai_client, status
            )
            results.append({
                'title': book.title,
                'pages': len(entry['pages']),
                'metadata_error': entry.get('metadata_error'),
                'extraction_failures': failed_pages,
            })
            overall.progress((index + 1) / max(total, 1))

        load_book_dict.clear()
        status.update(label=BatchBookEntry.creating_complete, state="complete")
    return results


def _render_upload(client, ai_available):
    st.write(BatchBookEntry.instructions)
    if not ai_available:
        st.warning(BatchBookEntry.no_api_key)

    # Direct browser-to-S3 upload (#118): replaces st.file_uploader so the whole
    # batch PUTs straight from the device to S3 at full resolution, bypassing the
    # websocket that drops on mobile. Mint a stable temp prefix
    # (uploads/batch/{session_id}/) once, then let the user pick HOW to fill it
    # (#143): upload from this device, or scan a QR and upload from a phone. Both
    # land in the SAME prefix, so "Detect books" below reads it either way.
    session_id = get_upload_session_id(UPLOAD_FLOW_KEY)

    upload_method = option_menu(
        None,
        [PhotoUpload.method_upload_here, PhotoUpload.method_go_to_phone],
        default_index=0,
        icons=['laptop', 'phone'],
        menu_icon="cast",
        orientation="horizontal",
        key="add_books_batch_upload_menu",
        styles=_UPLOAD_MENU_STYLES,
    )

    if upload_method == PhotoUpload.method_go_to_phone:
        render_go_to_phone(UPLOAD_FLOW_KEY, session_id)
    else:
        st.write(BatchBookEntry.direct_upload_instructions)
        put_urls = generate_put_urls(UPLOAD_FLOW_KEY, session_id)
        manifest_url = generate_manifest_put_url(UPLOAD_FLOW_KEY, session_id)
        st.iframe(build_uploader_html(put_urls, manifest_url), height=460)

    detect = st.button(BatchBookEntry.detect_button, key="add_books_batch_detect_button")
    if st.button(BatchBookEntry.cancel_button, key="add_books_batch_cancel_upload_button"):
        # Remove any photos already uploaded so they don't orphan in S3.
        cleanup_prefix(get_s3_filesystem(), UPLOAD_FLOW_KEY, session_id)
        _reset_batch_state()
        st.switch_page("./pages/user_home.py")

    if detect:
        fs = get_s3_filesystem()
        with st.spinner(BatchBookEntry.detecting):
            # Pull the uploaded batch (in page order: page_1..page_N as the browser
            # PUT them) into memory, then split it into per-book groups.
            pages = fetch_uploaded_photos(fs, UPLOAD_FLOW_KEY, session_id)
            if not pages:
                st.warning(BatchBookEntry.no_photos)
                return
            method, detected = _detect_books(pages, client)
        if not detected:
            st.warning(BatchBookEntry.no_books_detected)
            return
        # The batch is now in memory (batch_detected) and each book's pages are
        # re-saved to sawimages/{title}/ at create time, so the transient upload
        # buffer is no longer needed — drop the temp prefix and the session id.
        cleanup_prefix(fs, UPLOAD_FLOW_KEY, session_id)
        reset_upload_session(UPLOAD_FLOW_KEY)
        st.session_state['batch_method'] = method
        st.session_state['batch_detected'] = detected
        st.session_state['batch_step'] = 'review'
        st.rerun()


def _render_review(client):
    detected = st.session_state.get('batch_detected', [])
    method = st.session_state.get('batch_method', 'single')
    count = len(detected)

    st.header(BatchBookEntry.review_header)
    method_message = {
        'black_frame': BatchBookEntry.method_black_frame,
        'cover_page': BatchBookEntry.method_cover_page,
        'single': BatchBookEntry.method_single,
    }.get(method, BatchBookEntry.method_single)
    st.info(method_message.format(count=count))

    unreadable = sum(1 for entry in detected if entry.get('metadata_error'))
    if unreadable:
        st.warning(BatchBookEntry.review_metadata_warning.format(count=unreadable))

    for index, entry in enumerate(detected):
        metadata = entry.get('metadata') or {}
        title = (metadata.get('title') or "").strip() or \
            BatchBookEntry.untitled_title.format(n=index + 1)
        with st.expander(
            BatchBookEntry.book_summary.format(
                n=index + 1, title=title, pages=len(entry['pages'])
            ),
            expanded=True,
        ):
            if entry.get('metadata_error'):
                st.warning(BatchBookEntry.detail_metadata_error)
            authors = metadata.get('authors') or []
            illustrators = metadata.get('illustrators') or []
            publisher = metadata.get('publisher')
            year = metadata.get('published_year')
            if authors:
                st.write(BatchBookEntry.detail_author.format(value=", ".join(authors)))
            if illustrators:
                st.write(BatchBookEntry.detail_illustrator.format(value=", ".join(illustrators)))
            if publisher:
                st.write(BatchBookEntry.detail_publisher.format(value=publisher))
            if year:
                st.write(BatchBookEntry.detail_year.format(value=year))

    confirm = st.button(
        BatchBookEntry.confirm_button.format(count=count),
        key="add_books_batch_confirm_button",
    )
    if st.button(BatchBookEntry.start_over_button, key="add_books_batch_start_over_button"):
        _reset_batch_state()
        st.rerun()

    if confirm:
        fs = get_s3_filesystem()
        results = _create_books(detected, fs, client)
        st.session_state['batch_results'] = results
        st.session_state['batch_step'] = 'done'
        # Free the large image payloads now they are persisted to S3.
        st.session_state.pop('batch_detected', None)
        st.rerun()


def _render_done():
    results = st.session_state.get('batch_results', [])
    st.header(BatchBookEntry.done_header)

    needs_details = sum(1 for result in results if result.get('metadata_error'))
    st.success(BatchBookEntry.done_summary.format(count=len(results)))
    if needs_details:
        st.warning(BatchBookEntry.done_needs_details.format(count=needs_details))

    # One simple summary of pages the AI couldn't read across the whole batch, so
    # the user knows how many need manual entry (#132). Raw errors are never shown
    # — they are in the extraction_errors debug log.
    total_failed = sum(
        len(result.get('extraction_failures') or []) for result in results
    )
    if total_failed:
        st.warning(PhotoUpload.extraction_partial_fail_batch.format(count=total_failed))

    for result in results:
        if result.get('metadata_error'):
            # This book was created with a fallback title because its details
            # couldn't be read — flag it clearly so the user knows to finish it.
            st.write(BatchBookEntry.done_book_line_unread.format(
                title=result['title'], pages=result['pages']
            ))
        else:
            st.write(BatchBookEntry.done_book_line.format(
                title=result['title'], pages=result['pages']
            ))
    st.info(BatchBookEntry.done_note)

    home_col, again_col = st.columns(2)
    if home_col.button(BatchBookEntry.done_home_button, key="add_books_batch_done_home_button"):
        _reset_batch_state()
        st.switch_page("./pages/user_home.py")
    if again_col.button(BatchBookEntry.done_another_button, key="add_books_batch_done_another_button"):
        _reset_batch_state()
        st.rerun()


st.header(BatchBookEntry.header)

_client = get_anthropic_client()
_ai_available = _client is not None

_step = st.session_state.get('batch_step', 'upload')
if _step == 'review':
    _render_review(_client)
elif _step == 'done':
    _render_done()
else:
    _render_upload(_client, _ai_available)
