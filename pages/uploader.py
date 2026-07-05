import streamlit as st
import anthropic
from data_structures import Page, ExtractionErrorLog
from image_processing import (
    correct_book_page, check_crop_quality, get_rotation_angle, rotate_image,
    exif_transpose_bytes,
)
from text_content import Instructions, AIPrompts, BookPhotoEntry, Uploader, PhotoUpload
from utilities import (
    page_layout, check_authentication_status, extract_isbn, lookup_isbn,
    get_s3_filesystem, get_anthropic_client, vision_json, get_ai_settings,
)
from photo_upload import (
    get_upload_session_id,
    generate_put_urls,
    build_uploader_html,
    fetch_uploaded_photos,
    cleanup_prefix,
    reset_upload_session,
)

# Direct-to-S3 upload flow key (#118) for the shared page-photo / QR-phone upload
# widget. Namespaces its temp prefix (uploads/pages/{session_id}/) so it never
# collides with the other migrated surfaces (single / batch / collection).
UPLOAD_FLOW_KEY = "pages"


class PageExtractionError(Exception):
    """Raised when a page's AI text-extraction fails (#132).

    Covers the two genuine failure cases — an Anthropic API error, or a reply that
    cannot be parsed as usable JSON. The full detail has ALREADY been written to
    the ``extraction_errors`` debug log by the time this is raised, so callers just
    need to catch it, keep the page blank in the sequence, and count it. Carries
    the classified ``error_type`` and ``error_message`` for any caller that wants
    them, but the raw text is never shown to the user.
    """

    def __init__(self, error_type, error_message):
        super().__init__(error_message)
        self.error_type = error_type
        self.error_message = error_message


def extract_page_info(image_bytes, client, *, book=None, page_number=None,
                      page_name=None, flow=None):
    """Return (text, is_story_page, page_type) by sending image bytes to the
    DATA-EXTRACTION model (Claude Sonnet 5, #135).

    Wordless-page guard (audit item 2): the model now returns a ``has_text``
    boolean. Many picture-book pages are wordless full illustrations, and without
    this guard the model narrates the illustration into ``text`` — which is then
    stored as story text and pollutes the research word/character counts. When
    ``has_text`` is false the returned ``text`` is forced to ``""``.

    Uses the shared ``vision_json`` helper (#129) on ``EXTRACTION_MODEL`` with the
    higher ``EXTRACTION_MAX_EDGE`` (2576px) so dense page text is OCR'd at higher
    resolution than the ~1568px sweet spot. The corrected page image is still
    downscaled for the vision call (``downscale=True``) and JPEG re-encoded below
    Claude's 10 MB per-image limit (#134), so a large full-res page (e.g. a 16 MB
    hi-res photo) is no longer rejected with a 400 — which previously failed every
    page of such a book (#132 diagnosis).

    On an extraction FAILURE — an Anthropic API error, or a reply that cannot be
    parsed as usable JSON — the full detail (book, page, error type + message,
    username, flow, UTC timestamp) is written to the ``extraction_errors`` Firestore
    debug log and a :class:`PageExtractionError` is raised, so the caller can keep
    the page blank in the sequence, count it, and tell the user which pages need
    manual entry (#132). The raw API error is never surfaced to the user. This
    replaces the previous behaviour where API errors propagated raw and parse
    failures were silently saved as a blank, non-story page (#127).

    ``book``/``page_number``/``page_name``/``flow`` are optional logging context
    passed through by the caller; they do not affect the success path.
    """
    book_id = getattr(book, 'document_id', None) if book is not None else None
    book_title = getattr(book, 'title', None) if book is not None else None

    def _log_and_raise(error_type, error_message):
        ExtractionErrorLog.record(
            book_id=book_id,
            book_title=book_title,
            page_number=page_number,
            page_name=page_name,
            error_type=error_type,
            error_message=error_message,
            username=st.session_state.get('username'),
            flow=flow,
        )
        raise PageExtractionError(error_type, error_message)

    ai_settings = get_ai_settings()
    try:
        data, raw = vision_json(
            client, [image_bytes], AIPrompts.page_extraction, downscale=True,
            model=ai_settings['extraction_model'],
            max_edge=ai_settings['extraction_max_edge'],
            max_tokens=ai_settings['extraction_max_tokens'],
            label=flow or "page_extraction",
        )
    except anthropic.AnthropicError as exc:
        _log_and_raise(type(exc).__name__, str(exc))

    if not isinstance(data, dict):
        # ``vision_json`` already logged any JSON-decode failure and returned no
        # data; treat a missing/unparseable reply as an extraction failure rather
        # than silently saving a blank page.
        snippet = (raw or "").strip()
        message = (
            f"Model reply could not be parsed as usable JSON: {snippet[:500]}"
            if snippet else "Model returned no usable reply."
        )
        _log_and_raise(ExtractionErrorLog.ERROR_PARSE, message)

    # Wordless-page guard (audit item 2): when the model reports no story text,
    # store an empty string rather than whatever it may have written into
    # ``text`` (e.g. a description of the illustration). ``has_text`` defaults to
    # true when absent so a model reply that predates the field still behaves as
    # before.
    #
    # Bug fix: a "wordless page" reply sometimes carries ``"text": null`` (JSON
    # null / Python ``None``) rather than the requested ``""`` — the model is
    # prompted, not schema-constrained, so it does not always follow the "text
    # must be a string" instruction to the letter. ``dict.get(key, default)``
    # only substitutes ``default`` when the key is ABSENT, not when its value is
    # ``None``, so the previous unconditional ``.strip()`` raised an uncaught
    # ``AttributeError`` on such a reply. That escaped both this function's own
    # ``except anthropic.AnthropicError`` (it happens after the API call, parsing
    # the reply locally) and the caller's ``except PageExtractionError``, so it
    # crashed the whole per-page upload loop — every page after the wordless one
    # was left with neither an extraction attempt nor a Firestore ``pages`` doc,
    # which read as "OCR worked for the first half, then went blank" (see the
    # "Clean Up!" bug report: pages 1-12 fully extracted, 13-19 missing entirely,
    # with no matching ``extraction_errors`` entry — consistent with an
    # exception that skipped the existing error-logging path rather than a
    # genuine OCR failure). Any non-string ``text`` (``None`` or otherwise) is
    # now normalised to ``""`` instead of raising.
    raw_text = data.get("text")
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    if not bool(data.get("has_text", True)):
        text = ""

    return (
        text,
        bool(data.get("is_story_page", False)),
        data.get("page_type", ""),
    )


def _make_reporter(status, page_number, total, prefix=""):
    """Build a per-sub-step progress callback for one page (#110).

    Returns a callable taking a message *template* (e.g. ``Uploader.substep_*``)
    that updates the shared ``st.status`` label, formatting in this page's number
    and the batch total. Emitting one of these before every model call keeps the
    browser fed with frequent messages so the websocket does not drop to
    'Connecting…' during the long synchronous pipeline. A ``None`` status yields
    a no-op reporter so ``_process_page`` can still be called without a UI.
    """
    if status is None:
        return lambda _template: None

    def report(template):
        status.update(label=f"{prefix}{template.format(page=page_number, total=total)}")

    return report


def _process_page(raw_bytes, page_number, photos_url, fs, ai_client, report=None):
    """
    Run the staged correction pipeline for one page.

    Stage 1: OpenCV perspective correction + Haiku quality check.
    Stage 2: Rotation-only Sonnet fallback + Haiku quality check.

    ``report`` is an optional per-sub-step progress callback (see
    ``_make_reporter``) invoked before each model call so the frontend keeps
    receiving updates (#110).

    Model-call reduction (#110): when OpenCV returns a *high-confidence*,
    well-framed portrait crop we trust it and skip the Stage 1 Haiku
    crop-quality check, saving one model call on the dominant happy path. This
    is conservative — the high-confidence band (see ``correct_book_page``) only
    matches large, clearly upright single pages, where the Haiku verification
    almost always agrees — and it never touches the text-extraction step, so
    extraction accuracy is unchanged. Sideways/landscape/low-confidence crops
    still get the full Haiku check and the Sonnet rotation fallback.

    Returns (image_bytes_for_extraction, method) where method is
    'opencv', 'rotation', or None (no correction applied).
    Saves page_{n}_cropped.jpg to S3 when a stage succeeds.
    """
    report = report or (lambda _template: None)

    # Admin-configurable pipeline settings (models + feature toggles). When the
    # crop-quality gate is disabled the geometric OpenCV/rotation result is
    # trusted directly; when rotation correction is disabled the Sonnet rotation
    # pass is skipped entirely. Both default ON, so the pipeline is unchanged
    # unless an admin turns them off.
    ai_settings = get_ai_settings()
    crop_gate_on = ai_settings['enable_crop_quality_gate']
    rotation_on = ai_settings['enable_rotation_correction']
    crop_model = ai_settings['crop_quality_model']
    rotation_model = ai_settings['rotation_model']

    def _crop_ok(image_bytes):
        """Crop-quality gate: trust the geometry when the gate is disabled."""
        if not crop_gate_on:
            return True
        report(Uploader.substep_checking_crop)
        return check_crop_quality(image_bytes, ai_client, model=crop_model)

    def _save_and_return(corrected, method):
        with fs.open(f"{photos_url}/page_{page_number}_cropped.jpg", 'wb') as f:
            f.write(corrected)
        return corrected, method

    # Stage 1 — OpenCV perspective correction
    report(Uploader.substep_correcting)
    corrected_bytes, opencv_ok, high_confidence = correct_book_page(raw_bytes)
    if opencv_ok:
        if high_confidence:
            # Trust the geometry and skip the Haiku verification (one fewer call).
            return _save_and_return(corrected_bytes, 'opencv')
        if _crop_ok(corrected_bytes):
            return _save_and_return(corrected_bytes, 'opencv')

    # Stage 2 — rotation-only fallback via Sonnet.
    #
    # Rotation fix (audit item 3): when the model detects a non-zero rotation the
    # rotated bytes are the CORRECT orientation for OCR and must be used
    # unconditionally. Previously the rotated bytes were only used if
    # ``check_crop_quality`` passed — but that prompt requires the page to fill
    # the frame / not be cropped, which a rotation-only raw phone photo (still
    # showing table/hands/background) routinely fails, so the UPSIDE-DOWN original
    # was silently sent to OCR instead. The crop-quality check now only gates the
    # OPTIONAL saving of the ``_cropped`` artifact, never which bytes go to OCR.
    if rotation_on:
        report(Uploader.substep_detecting_rotation)
        angle = get_rotation_angle(raw_bytes, ai_client, model=rotation_model)
        if angle != 0:
            rotated_bytes = rotate_image(raw_bytes, angle)
            if _crop_ok(rotated_bytes):
                # Well-framed after rotation — save the corrected artifact too.
                return _save_and_return(rotated_bytes, 'rotation')
            # Not well-framed (background/cropping), but the orientation is now
            # correct — use the rotated bytes for extraction regardless.
            return rotated_bytes, 'rotation'

    return raw_bytes, None


def _process_photo_batch(raw_bytes_list, sort_file_names, fs):
    """Run the upload + correction + extraction pipeline for one batch of page
    photos (already read into memory, in page order).

    Writes raw and corrected images to S3, registers Page docs, extracts text, and
    performs the copyright-page ISBN lookup. Shared by both the manual file-upload
    path and the photo-first reuse path (#59). The caller guards against re-running
    via '_upload_pipeline_done', which this function sets on completion.
    """
    total = len(sort_file_names)
    photos_url = f"sawimages/{st.session_state['current_book'].title}"
    copyright_text = None
    # Page numbers whose AI text-extraction failed (#132): the pages stay in the
    # sequence as blanks and are surfaced to the user for manual entry afterwards.
    failed_pages = []

    # One live st.status drives the whole pipeline. Updating its label at every
    # sub-step (upload → correct → check → extract) sends the browser frequent
    # messages, which keeps the websocket alive instead of dropping to
    # 'Connecting…' on slow/mobile links during the long synchronous run (#110).
    with st.status(Uploader.status_header, expanded=True) as status:
        progress = st.progress(0.0)

        # Phase 1 — upload raw photos to S3
        corrected_bytes_list = []
        for fi, raw_bytes in enumerate(raw_bytes_list):
            status.update(label=Uploader.saving_photo.format(current=fi + 1, total=total))
            # Normalise orientation so the stored photo and every downstream stage
            # (correction, extraction, display) work on correctly-oriented pixels
            # (fixes portrait photos, #51). Idempotent — a no-op once the EXIF tag
            # is baked in — so it's safe for both the manual-upload and photo-first
            # reuse paths that share this function.
            raw_bytes = exif_transpose_bytes(raw_bytes)
            corrected_bytes_list.append(raw_bytes)
            with fs.open(f"{photos_url}/page_{fi + 1}.jpg", 'wb') as f:
                f.write(raw_bytes)
            progress.progress((fi + 1) / total)
        # Downstream correction/extraction should use the orientation-corrected bytes.
        raw_bytes_list = corrected_bytes_list

        st.session_state.current_book.photos_uploaded = True
        st.session_state.current_book.photos_url = photos_url
        st.session_state.current_book.page_count = total
        status.update(label=Uploader.photos_saved)

        # Phase 2 — image correction + text extraction per page
        ai_client = get_anthropic_client()
        if ai_client is not None:
            for i, raw_bytes in enumerate(raw_bytes_list):
                page_number = i + 1
                report = _make_reporter(status, page_number, total)

                bytes_for_extraction, _method = _process_page(
                    raw_bytes, page_number, photos_url, fs, ai_client, report
                )

                # Set the page's fields on the UNREGISTERED object first, then
                # register() once (audit item 8 — write amplification): otherwise
                # register() writes the doc and each of ``text``/``contains_story``
                # then write-throughs a separate Firestore update. Both branches
                # register exactly once, so the stored result is identical with
                # one write instead of several.
                page = Page(
                    page_number=page_number,
                    book=st.session_state['current_book'].title
                )

                report(Uploader.substep_extracting)
                try:
                    text, is_story, page_type = extract_page_info(
                        bytes_for_extraction, ai_client,
                        book=st.session_state['current_book'],
                        page_number=page_number,
                        page_name=f"page_{page_number}.jpg",
                        flow=ExtractionErrorLog.FLOW_SINGLE,
                    )
                except PageExtractionError:
                    # Detail already logged to extraction_errors; keep the blank
                    # page in the sequence and record it for the user (#132).
                    page.register()
                    failed_pages.append(page_number)
                else:
                    if text:
                        page.text = text
                    page.contains_story = is_story
                    page.register()

                    if page_type == 'copyright' and text and copyright_text is None:
                        copyright_text = text

                progress.progress((i + 1) / total)

            status.update(label=Uploader.processing_complete, state="complete")
        else:
            # No API key — register pages without extraction
            for i in range(total):
                page = Page(
                    page_number=i + 1,
                    book=st.session_state['current_book'].title
                )
                page.register()
            status.update(label=Uploader.processing_complete, state="complete")

    # If OCR was skipped wholesale because no API key is configured, tell the user
    # explicitly rather than silently registering blank pages (#153) — the batch
    # and reconstruction flows already warn on a missing key, so the single-book
    # flow must too, otherwise text recognition "just doesn't work" with no message.
    if ai_client is None:
        st.warning(PhotoUpload.no_api_key)

    # Tell the user (once) which pages the AI could not read, so they know where
    # to focus manual entry (#132). The raw errors are never shown — they are in
    # the extraction_errors debug log. Rendered outside the st.status block so it
    # is a normal page message, not hidden inside the collapsed status container.
    if failed_pages:
        st.warning(PhotoUpload.extraction_partial_fail.format(
            failed=len(failed_pages), total=total,
            pages=", ".join(str(p) for p in failed_pages),
        ))

    # ISBN lookup — use the copyright page text to fetch book metadata and
    # pre-populate the Add Book form. Done outside the st.status block so the
    # resulting st.info renders as a normal page message, not hidden inside the
    # (now collapsed) status container.
    if copyright_text:
        isbn = extract_isbn(copyright_text)
        if isbn:
            isbn_metadata = lookup_isbn(isbn)
            if isbn_metadata:
                st.session_state['isbn_metadata'] = isbn_metadata
                st.info(
                    Uploader.isbn_metadata_found.format(isbn=isbn, title=isbn_metadata['title'])
                )

    st.session_state['_upload_pipeline_done'] = True


def _cleanup_upload_buffer(fs, flow_key):
    """Delete this session's temp ``uploads/{flow_key}/{session_id}/`` buffer once
    its photos have been written into ``sawimages/{title}/``, and drop the session
    id so a new entry mints a fresh prefix (#124).

    Scoped strictly to THIS browser session's ``flow_key`` (the session id is read
    from ``st.session_state``), so it can never clear another user's or another
    session's prefix. A no-op when no session id is present. ``cleanup_prefix``
    swallows/logs any S3 failure, so this never breaks the just-completed entry.
    """
    session_id = st.session_state.get(f"upload_session_{flow_key}")
    if not session_id:
        return
    cleanup_prefix(fs, flow_key, session_id)
    reset_upload_session(flow_key)


def upload_widget(on_submit='enter_text', auto_forward=False):

    fs = get_s3_filesystem()

    def upload_page_photos():
        # Photos captured in the photo-first flow (#59) are reused here so the
        # user does not have to upload them a second time.
        stashed = st.session_state.get('photo_first_pages')
        # Streamlit re-runs on every interaction; the pipeline must run only once.
        done = st.session_state.get('_upload_pipeline_done', False)

        if stashed:
            if not done:
                st.write(BookPhotoEntry.reuse_notice.format(count=len(stashed)))
                sort_file_names = [name for name, _ in stashed]
                raw_bytes_list = [data for _, data in stashed]
                _process_photo_batch(raw_bytes_list, sort_file_names, fs)
                # Pages are now in sawimages/{title}/. The photo-first reuse path
                # was fed from the "single" flow's temp buffer (uploaded in
                # add_book_photos.py), so clear it now rather than waiting on the
                # "Continue" click below (which the user may never reach) (#124).
                _cleanup_upload_buffer(fs, "single")

            # Photo-first flow (#151): the pages are now processed, written to
            # sawimages/{title}/ and registered as Page docs, so go STRAIGHT to
            # text entry instead of making the user click through another
            # "add photos" page. Mirrors the cleanup the manual "Continue" does
            # below. switch_page halts this run, so nothing after it executes.
            if auto_forward and on_submit == 'enter_text':
                st.session_state.pop('_upload_pipeline_done', None)
                st.session_state.pop('book_pages_dict', None)
                st.session_state.pop('photo_first_pages', None)
                st.session_state['current_page_number'] = 1
                st.switch_page("./pages/enter_text.py")
        else:
            # Direct browser-to-S3 upload (#114/#118): replaces st.file_uploader,
            # which drops the Streamlit websocket on mobile while the native photo
            # picker is full-screen. Mint a stable per-session temp prefix
            # (uploads/pages/{session_id}/) + presigned PUT URLs, render the
            # browser-side uploader (each photo PUTs straight from the device to
            # S3, full-resolution), then discover what landed by listing the S3
            # prefix when the user taps "Process photos".
            st.write(Uploader.direct_upload_instructions)
            session_id = get_upload_session_id(UPLOAD_FLOW_KEY)
            put_urls = generate_put_urls(UPLOAD_FLOW_KEY, session_id)
            st.iframe(build_uploader_html(put_urls), height=460)

            process = st.button(Uploader.process_button, key="uploader_process_button")
            if not done:
                if not process:
                    return
                with st.spinner(BookPhotoEntry.reading_photos):
                    pages = fetch_uploaded_photos(fs, UPLOAD_FLOW_KEY, session_id)
                if not pages:
                    st.warning(Uploader.no_photos_uploaded)
                    return
                # fetch_uploaded_photos already returns the photos in page order
                # (natsorted page_1..page_N as the browser PUT them).
                sort_file_names = [name for name, _ in pages]
                raw_bytes_list = [data for _, data in pages]
                _process_photo_batch(raw_bytes_list, sort_file_names, fs)
                # Pages are now in sawimages/{title}/, so the direct page-upload
                # temp buffer (uploads/pages/{session_id}/) is no longer needed —
                # clear it here rather than waiting on the "Continue" click below
                # (which the user may abandon), which is what left prefixes to pile
                # up (#124).
                _cleanup_upload_buffer(fs, UPLOAD_FLOW_KEY)

        st.write(Uploader.upload_complete)
        submit = st.button(Uploader.continue_button, key="uploader_continue_button")

        if submit:
            st.session_state.pop('_upload_pipeline_done', None)
            st.session_state.pop('book_pages_dict', None)
            st.session_state.pop('photo_first_pages', None)
            # The temp upload buffers were already cleared right after the photos
            # were written to sawimages/{title}/ (see the _cleanup_upload_buffer
            # calls above), so nothing to clean here — just leave the flow.
            if on_submit == 'enter_text':
                st.switch_page("./pages/enter_text.py")
            else:
                st.success(Instructions.upload_success_return)

    upload_page_photos()


# Page-level code runs only when uploader.py is the active page (Streamlit sets
# __name__ == "__main__" for the navigated page). Guarding this prevents the page
# from rendering when the module is merely imported for `upload_widget`
# (e.g. by page_photo_upload.py), which previously rendered the sidebar/back
# button twice and raised StreamlitDuplicateElementId.
if __name__ == "__main__":
    check_authentication_status()
    page_layout()
