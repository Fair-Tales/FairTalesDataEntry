import logging
from functools import partial

import streamlit as st
import anthropic
from data_structures import Page, ExtractionErrorLog, log_extraction_error
from image_processing import exif_transpose_bytes, correct_page_image, make_display_copy
from text_content import Instructions, AIPrompts, BookPhotoEntry, Uploader, PhotoUpload
from utilities import (
    page_layout, check_authentication_status, extract_isbn, lookup_isbn,
    get_s3_filesystem, get_anthropic_client, vision_json, get_ai_settings,
    mark_character_autodetect_pending,
)
from background_pipeline import (
    JOB_STATE_KEY as BACKGROUND_JOB_KEY,
    get_active_job, ensure_worker_running, worker_alive_for, stamp_book_id,
    wait_for_page_result, wait_for_character_suggestions, reconcile_s3_prefix,
    finalize_job, clear_page_processing_job,
)
from photo_upload import (
    get_upload_session_id,
    generate_put_urls,
    generate_manifest_put_url,
    build_uploader_html,
    fetch_uploaded_photos,
    cleanup_prefix,
    reset_upload_session,
    uploads_settled,
    render_go_to_phone,
)
from s3_constants import book_folder_name, max_folder_page

# Direct-to-S3 upload flow key (#118) for the shared page-photo / QR-phone upload
# widget. Namespaces its temp prefix (uploads/pages/{session_id}/) so it never
# collides with the other migrated surfaces (single / batch / collection).
UPLOAD_FLOW_KEY = "pages"

# Separate flow key for the append-more-photos surface (#203) so an in-flight
# append upload can never mix with a full page (re-)upload's temp prefix.
APPEND_FLOW_KEY = "append"

logger = logging.getLogger(__name__)


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


def attempt_page_extraction(image_bytes, client, ai_settings, *, label):
    """OCR one page image and return a classified outcome — with NO Firestore
    logging, no session access and no exception raised, so it is callable from
    the background page-processing worker (#179) as well as from
    :func:`extract_page_info` (which adds the logging + raise contract).

    Returns ``('ok', (text, is_story_page, page_type))`` on success, or
    ``('error', (error_type, error_message))`` on an Anthropic API error or a
    reply that cannot be parsed as usable JSON.

    ``ai_settings`` is a plain validated settings dict, read by the CALLER (the
    worker thread must not touch the ``get_ai_settings`` Streamlit cache).

    Uses the shared ``vision_json`` helper (#129) with the admin-configured
    extraction model/resolution/tokens (#135). The image is downscaled for the
    vision call and JPEG re-encoded below Claude's 10 MB per-image limit (#134).

    Wordless-page guard (audit item 2): the model returns a ``has_text``
    boolean. Many picture-book pages are wordless full illustrations, and
    without this guard the model narrates the illustration into ``text`` —
    polluting the research word/character counts — so when ``has_text`` is
    false the returned ``text`` is forced to ``""``. A "wordless page" reply
    may also carry ``"text": null`` rather than the requested ``""`` (the model
    is prompted, not schema-constrained), so any non-string ``text`` is
    normalised to ``""`` rather than crashing the page loop (see the "Clean
    Up!" bug: an unguarded ``None.strip()`` here blanked the second half of a
    book).
    """
    try:
        data, raw = vision_json(
            client, [image_bytes], AIPrompts.page_extraction, downscale=True,
            model=ai_settings['extraction_model'],
            max_edge=ai_settings['extraction_max_edge'],
            max_tokens=ai_settings['extraction_max_tokens'],
            label=label,
        )
    except anthropic.AnthropicError as exc:
        return 'error', (type(exc).__name__, str(exc))

    if not isinstance(data, dict):
        # ``vision_json`` already logged any JSON-decode failure and returned no
        # data; treat a missing/unparseable reply as an extraction failure rather
        # than silently saving a blank page.
        snippet = (raw or "").strip()
        message = (
            f"Model reply could not be parsed as usable JSON: {snippet[:500]}"
            if snippet else "Model returned no usable reply."
        )
        return 'error', (ExtractionErrorLog.ERROR_PARSE, message)

    raw_text = data.get("text")
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    if not bool(data.get("has_text", True)):
        text = ""

    return 'ok', (
        text,
        bool(data.get("is_story_page", False)),
        data.get("page_type", ""),
    )


def extract_page_info(image_bytes, client, *, book=None, page_number=None,
                      page_name=None, flow=None):
    """Return (text, is_story_page, page_type) by sending image bytes to the
    DATA-EXTRACTION model — see :func:`attempt_page_extraction` for the call
    itself and the wordless-page normalisation.

    On an extraction FAILURE — an Anthropic API error, or a reply that cannot be
    parsed as usable JSON — the full detail (book, page, error type + message,
    username, flow, UTC timestamp) is written to the ``extraction_errors``
    Firestore debug log and a :class:`PageExtractionError` is raised, so the
    caller can keep the page blank in the sequence, count it, and tell the user
    which pages need manual entry (#132). The raw API error is never surfaced to
    the user; the logging routes through the shared ``log_extraction_error``
    (#129, harden-page-loop-error-logging) so this path and the per-page
    isolation boundary in ``_process_photo_batch`` write the same shape to the
    same ``extraction_errors`` collection.

    ``book``/``page_number``/``page_name``/``flow`` are optional logging context
    passed through by the caller; they do not affect the success path.
    """
    ai_settings = get_ai_settings()
    outcome, payload = attempt_page_extraction(
        image_bytes, client, ai_settings, label=flow or "page_extraction",
    )
    if outcome != 'ok':
        error_type, error_message = payload
        log_extraction_error(
            book=book,
            page_number=page_number,
            page_name=page_name,
            error_type=error_type,
            error_message=error_message,
            flow=flow,
            model=ai_settings['extraction_model'],
        )
        raise PageExtractionError(error_type, error_message)
    return payload


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
    Run the staged correction pipeline for one page and persist the artifact.

    The pipeline itself (OpenCV perspective correction + Haiku crop-quality
    check + rotation checks/fallback, with the admin-configurable models and
    feature toggles) lives in ``image_processing.correct_page_image`` so the
    background page-processing worker (#179) can run the identical stages
    without S3/Streamlit access — see that function for the full staging and
    orientation rationale (#110/#181). This wrapper adds the S3 side effect.

    ``report`` is an optional per-sub-step progress callback (see
    ``_make_reporter``) invoked before each model call so the frontend keeps
    receiving updates (#110).

    Returns (image_bytes_for_extraction, method, rotation_uncertain) where
    method is 'opencv', 'rotation', or None (no correction applied) and
    rotation_uncertain (#217) is True when the orientation check could not be
    trusted for this page (persisted onto the Page doc by the caller).
    Saves page_{n}_cropped.jpg to S3 whenever a stage produces a corrected
    image — including a rotation-only result with no crop.
    """
    bytes_for_extraction, corrected, method, rotation_uncertain = correct_page_image(
        raw_bytes, ai_client, get_ai_settings(), report
    )
    if corrected is not None:
        with fs.open(f"{photos_url}/page_{page_number}_cropped.jpg", 'wb') as f:
            f.write(corrected)
    # Screen-sized display derivative (#184): enter-text ships this instead of
    # the multi-MB original. Derived from the corrected image when one exists
    # (what enter-text shows by default), else the oriented raw page.
    with fs.open(f"{photos_url}/page_{page_number}_display.jpg", 'wb') as f:
        f.write(make_display_copy(corrected if corrected is not None else raw_bytes))
    return bytes_for_extraction, method, rotation_uncertain


def _consume_background_result(result, page_number, model):
    """Turn one page's durable background result (#179) into the extraction
    triple on the script thread. The page's raw + corrected images are ALREADY
    in S3 at the (now reconciled) final prefix — the worker wrote them as it
    processed, so there is no image write here.

    A failure the worker recorded for this page is logged to
    ``extraction_errors`` HERE (the worker thread has no session access) through
    the same shared ``log_extraction_error`` helper as the inline path, then
    re-raised as :class:`PageExtractionError` so the caller's existing
    blank-page/failed-pages handling applies unchanged (#132).
    """
    outcome, payload, _method, corrected, rotation_uncertain = result
    if outcome != 'ok':
        error_type, error_message = payload
        log_extraction_error(
            book=st.session_state['current_book'],
            page_number=page_number,
            page_name=f"page_{page_number}.jpg",
            error_type=error_type,
            error_message=error_message,
            flow=ExtractionErrorLog.FLOW_SINGLE,
            model=model,
        )
        raise PageExtractionError(error_type, error_message)
    text, is_story, page_type = payload
    return text, is_story, page_type, bool(corrected), bool(rotation_uncertain)


def _register_placeholder_page(page, page_number):
    """Best-effort registration of a blank placeholder ``Page`` after the
    per-page isolation boundary in ``_process_photo_batch`` catches a failure.

    ``enter_text.py``'s ``create_page_dict_from_db`` fetches a Page doc by a
    deterministic ``{book_id}_{page_number}`` id for EVERY page number from 1
    to ``page_count`` — it does not tolerate a missing doc. So even when a
    page's processing failed, it must still end up with SOME registered doc
    (blank is fine — it is already flagged via ``failed_pages`` /
    ``extraction_errors``) so later page numbers are not shifted or broken.

    Guarded so a SECOND failure here (e.g. Firestore itself is unavailable,
    which may be the very reason the original ``register()`` call failed
    inside the isolation boundary) cannot re-raise and abort the batch. The
    triggering failure is already logged to ``extraction_errors`` by the
    caller; this fallback failure is only logged locally via the standard
    logger, since a Firestore write is unlikely to succeed here either.
    """
    try:
        if not page.is_registered:
            page.register()
    except Exception as exc:  # noqa: BLE001 - best-effort fallback, see docstring
        logger.warning(
            "Failed to register placeholder Page %s after isolation catch: %s",
            page_number, exc,
        )


def _register_processed_page(page_number, produce, model):
    """Run the per-page isolation boundary ONCE and register the Page (#129 —
    shared by the durable-job finalize path and the inline pipeline so the
    identical error handling lives in one place).

    ``produce`` is a zero-arg callable returning
    ``(text, is_story, page_type, corrected, rotation_uncertain)`` or raising
    :class:`PageExtractionError`. ``corrected`` (whether an auto-corrected image
    was written, #184) is recorded on the Page so enter-text can skip the S3 HEAD
    check; ``rotation_uncertain`` (#217) is recorded so enter-text/validation can
    surface pages whose orientation the automatic check could not decide.
    Returns ``(status, copyright_text)``
    where ``status`` is ``'ok'`` / ``'failed'`` and ``copyright_text`` is the
    page's text when it is the copyright page (else ``None``). Never raises.

    The broad ``except Exception`` is the documented exception to the
    narrow-except rule (CLAUDE.md): its whole job is to be an isolation boundary
    around ONE page so a single failure can never blank out the rest of the
    book. It is NOT a silent swallow — every failure is logged to
    ``extraction_errors`` via the shared helper, the page is still registered
    blank (best-effort) so page numbering stays intact, and the caller records
    it in ``failed_pages`` for the user-facing "page N couldn't be read"
    warning (#132). The nested ``except PageExtractionError`` is the
    pre-existing (#132) extraction-failure path, already logged by its source.
    """
    current_book = st.session_state['current_book']
    # Set fields on the UNREGISTERED object first, then register() once (audit
    # item 8 — write amplification): otherwise register() writes the doc and
    # each field then write-throughs a separate Firestore update.
    page = Page(page_number=page_number, book=current_book.title)
    try:
        try:
            text, is_story, page_type, corrected, rotation_uncertain = produce()
        except PageExtractionError:
            # Detail already logged to extraction_errors; keep the blank page in
            # the sequence and record it for the user (#132). ``corrected`` stays
            # None (unknown) so enter-text falls back to the S3 check for it.
            page.register()
            return 'failed', None
        if text:
            page.text = text
        page.contains_story = is_story
        page.corrected = corrected
        page.rotation_uncertain = rotation_uncertain
        page.register()
        return 'ok', (text if (page_type == 'copyright' and text) else None)
    except Exception as exc:  # noqa: BLE001 - per-page isolation boundary, see docstring
        log_extraction_error(
            book=current_book,
            page_number=page_number,
            page_name=f"page_{page_number}.jpg",
            error_type=type(exc).__name__,
            error_message=str(exc),
            flow=ExtractionErrorLog.FLOW_SINGLE,
            model=model,
        )
        _register_placeholder_page(page, page_number)
        return 'failed', None


def _finalize_job_batch(job, raw_bytes_list, total, fs, db, ai_client, model,
                        status, progress):
    """Finalise a durable background job (#179): collect each page's persisted
    result, reconcile the S3 folder to the final book title, register the Page
    docs, and hand over the precomputed character suggestions.

    The worker has been correcting/OCR-ing straight to S3 + Firestore, so there
    is almost no work left here — this mostly waits (usually already done),
    renames the S3 folder if the user edited the title, and does the
    script-thread-only Firestore ``register()`` writes. Pages the worker could
    not produce (it died before reaching them) fall back to inline processing.
    Returns ``(copyright_text, failed_pages)``.
    """
    current_book = st.session_state['current_book']
    copyright_text = None
    failed_pages = []
    settings = get_ai_settings()

    # Make sure a worker is advancing this job in THIS process — resume it if the
    # starting session's worker died or this is a later/other session — and link
    # the now-known book id to the durable job for cross-session discovery.
    ensure_worker_running(fs, db, ai_client, settings, job, raw_bytes_list)
    stamp_book_id(db, job, current_book.document_id)
    alive = worker_alive_for(job)

    # Wait for EVERY page's durable result before touching the S3 layout, so the
    # worker is guaranteed to have stopped writing into the working prefix by the
    # time we reconcile it. Each result is (outcome, payload, method, corrected,
    # rotation_uncertain) or None (worker could not produce it -> inline
    # fallback below).
    results = {}
    for page_number in range(1, total + 1):
        status.update(label=Uploader.substep_collecting_result.format(page=page_number, total=total))
        results[page_number] = wait_for_page_result(
            db, job, page_number, worker_alive=alive,
            on_wait=partial(
                lambda p: status.update(
                    label=Uploader.substep_collecting_result.format(page=p, total=total)),
                page_number,
            ),
        )
        progress.progress(page_number / total)

    # Reconcile the S3 working folder to the final book-title folder (rename if
    # the user edited the title on the metadata form, #179) BEFORE any register /
    # inline write, so both worker-written and fallback pages land under it.
    final_title = current_book.title
    photos_url = reconcile_s3_prefix(fs, job, final_title)

    current_book.photos_uploaded = True
    current_book.photos_url = photos_url
    current_book.page_count = total

    for page_number in range(1, total + 1):
        report = _make_reporter(status, page_number, total)
        result = results[page_number]

        def _produce(result=result, page_number=page_number, report=report):
            if result is not None:
                # Images already in S3 at the final prefix (worker wrote them,
                # reconcile moved them) — just classify + return the result.
                return _consume_background_result(result, page_number, model)
            # Fallback: the worker never produced this page. Process it inline
            # now, writing raw + corrected under the final prefix.
            raw_bytes = exif_transpose_bytes(raw_bytes_list[page_number - 1])
            with fs.open(f"{photos_url}/page_{page_number}.jpg", 'wb') as f:
                f.write(raw_bytes)
            bytes_for_extraction, method, rotation_uncertain = _process_page(
                raw_bytes, page_number, photos_url, fs, ai_client, report
            )
            report(Uploader.substep_extracting)
            text, is_story, page_type = extract_page_info(
                bytes_for_extraction, ai_client, book=current_book,
                page_number=page_number, page_name=f"page_{page_number}.jpg",
                flow=ExtractionErrorLog.FLOW_SINGLE,
            )
            return text, is_story, page_type, method is not None, rotation_uncertain

        page_status, cp = _register_processed_page(page_number, _produce, model)
        if page_status == 'failed':
            failed_pages.append(page_number)
        elif cp and copyright_text is None:
            copyright_text = cp
        progress.progress(page_number / total)

    # Precomputed whole-book character detection (#170/#182/#183): hand the
    # worker's suggestions to enter-text so the review form appears instantly.
    # ``None`` (detection failed / skipped / no worker left) leaves enter-text to
    # run detection live, exactly as before. Keyed by book id so a stale stash
    # can never surface for a different book.
    status.update(label=Uploader.detecting_characters)
    suggestions = wait_for_character_suggestions(db, job, worker_alive=alive)
    if suggestions is not None:
        st.session_state['_precomputed_character_suggestions'] = {
            'book_id': current_book.document_id,
            'suggestions': suggestions,
        }
    mark_character_autodetect_pending(st.session_state)

    finalize_job(db, job, final_title)
    clear_page_processing_job(st.session_state)
    status.update(label=Uploader.processing_complete, state="complete")
    return copyright_text, failed_pages


def _inline_ai_batch(raw_bytes_list, total, fs, ai_client, model, status, progress,
                     start_page=0):
    """Inline (no background job) upload + per-page correction/OCR pipeline —
    the path taken by QR / manual re-upload of an existing book, and the fallback
    when no durable job exists. Returns ``(copyright_text, failed_pages)``.

    ``start_page`` (#203): number of pages the book ALREADY has. The default 0
    is the fresh-upload case (pages numbered from 1); the append flow passes the
    current maximum so the new photos become pages ``start_page+1 …
    start_page+total`` and no existing ``page_N`` file or doc is ever touched.
    """
    current_book = st.session_state['current_book']
    photos_url = f"sawimages/{current_book.title}"
    copyright_text = None
    failed_pages = []

    # Phase 1 — upload raw photos to S3 (orientation-normalised, #51). Idempotent.
    oriented_list = []
    for fi, raw_bytes in enumerate(raw_bytes_list):
        status.update(label=Uploader.saving_photo.format(current=fi + 1, total=total))
        raw_bytes = exif_transpose_bytes(raw_bytes)
        oriented_list.append(raw_bytes)
        with fs.open(f"{photos_url}/page_{start_page + fi + 1}.jpg", 'wb') as f:
            f.write(raw_bytes)
        progress.progress((fi + 1) / total)
    raw_bytes_list = oriented_list

    current_book.photos_uploaded = True
    current_book.photos_url = photos_url
    current_book.page_count = start_page + total
    status.update(label=Uploader.photos_saved)

    # Phase 2 — image correction + text extraction per page.
    for i, raw_bytes in enumerate(raw_bytes_list):
        page_number = start_page + i + 1
        report = _make_reporter(status, i + 1, total)

        def _produce(raw_bytes=raw_bytes, page_number=page_number, report=report):
            bytes_for_extraction, method, rotation_uncertain = _process_page(
                raw_bytes, page_number, photos_url, fs, ai_client, report
            )
            report(Uploader.substep_extracting)
            text, is_story, page_type = extract_page_info(
                bytes_for_extraction, ai_client, book=current_book,
                page_number=page_number, page_name=f"page_{page_number}.jpg",
                flow=ExtractionErrorLog.FLOW_SINGLE,
            )
            return text, is_story, page_type, method is not None, rotation_uncertain

        page_status, cp = _register_processed_page(page_number, _produce, model)
        if page_status == 'failed':
            failed_pages.append(page_number)
        elif cp and copyright_text is None:
            copyright_text = cp
        progress.progress((i + 1) / total)

    # Auto-run character detection the next time this book's enter-text page
    # loads (#170), now that OCR has run and there is story text to work with.
    mark_character_autodetect_pending(st.session_state)
    status.update(label=Uploader.processing_complete, state="complete")
    return copyright_text, failed_pages


def _blank_batch(raw_bytes_list, total, fs, status, progress, start_page=0):
    """No-API-key path: upload raw photos and register blank pages (no OCR).
    Returns ``failed_pages``.

    ``start_page`` (#203): as for ``_inline_ai_batch`` — 0 for a fresh upload,
    the book's current maximum page number when appending.
    """
    current_book = st.session_state['current_book']
    photos_url = f"sawimages/{current_book.title}"
    failed_pages = []

    # Upload raw photos (orientation-normalised, #51) so the pages still have
    # images to enter text against even with no OCR.
    for fi, raw_bytes in enumerate(raw_bytes_list):
        status.update(label=Uploader.saving_photo.format(current=fi + 1, total=total))
        oriented = exif_transpose_bytes(raw_bytes)
        with fs.open(f"{photos_url}/page_{start_page + fi + 1}.jpg", 'wb') as f:
            f.write(oriented)
        # Display derivative (#184): no correction runs on this path, so it is
        # derived from the oriented raw page.
        with fs.open(f"{photos_url}/page_{start_page + fi + 1}_display.jpg", 'wb') as f:
            f.write(make_display_copy(oriented))
        progress.progress((fi + 1) / total)

    current_book.photos_uploaded = True
    current_book.photos_url = photos_url
    current_book.page_count = start_page + total
    for page_number in range(start_page + 1, start_page + total + 1):
        page = Page(page_number=page_number, book=current_book.title)
        # No auto-correction on the no-API-key path, so there is never a cropped
        # image — record corrected=False so enter-text skips the S3 HEAD check.
        page.corrected = False
        try:
            page.register()
        except Exception as exc:  # noqa: BLE001 - isolation boundary, one bad register must not abort the batch
            log_extraction_error(
                book=current_book,
                page_number=page_number,
                page_name=f"page_{page_number}.jpg",
                error_type=type(exc).__name__,
                error_message=str(exc),
                flow=ExtractionErrorLog.FLOW_SINGLE,
            )
            failed_pages.append(page_number)
    status.update(label=Uploader.processing_complete, state="complete")
    return failed_pages


def _process_photo_batch(raw_bytes_list, sort_file_names, fs):
    """Run the upload + correction + extraction pipeline for one batch of page
    photos (already read into memory, in page order).

    Writes raw and corrected images to S3, registers Page docs, extracts text, and
    performs the copyright-page ISBN lookup. Shared by both the manual file-upload
    path and the photo-first reuse path (#59). The caller guards against re-running
    via '_upload_pipeline_done', which this function sets on completion.

    Three sub-paths (#179):
      * a durable background JOB exists for these photos -> finalise it
        (``_finalize_job_batch``): its worker already corrected/OCR'd them to S3
        + Firestore while the user filled in metadata;
      * no job but an API key -> the inline pipeline (``_inline_ai_batch``);
      * no API key -> register blank pages (``_blank_batch``).
    """
    total = len(sort_file_names)
    model = get_ai_settings()['extraction_model']
    copyright_text = None
    failed_pages = []

    # Invalidate enter-text's cached page images (#199): this pipeline is about
    # to (re)write sawimages/{title}/page_N(.jpg|_cropped|_display) — on a
    # RE-upload the @st.cache_data image cache would otherwise keep serving the
    # previous upload's image per page, which read as wrong page order that
    # "fixed itself" as entries evicted. enter_text.load_image cannot be
    # imported here (enter_text imports this module), so the clear is staged
    # via session state and consumed at the top of enter_text.
    st.session_state['_invalidate_image_cache'] = True

    ai_client = get_anthropic_client()

    # Recognise this session's durable background job by a fingerprint of the
    # ORIGINAL raw bytes (read before any orientation-normalisation). ``db`` is a
    # raw firestore client obtained from the session wrapper on THIS (script)
    # thread and handed to any resumed worker. ``None`` when there is no job /
    # no API key -> inline path.
    db = None
    job = None
    if (
        ai_client is not None
        and BACKGROUND_JOB_KEY in st.session_state
        and 'firestore' in st.session_state
    ):
        db = st.session_state.firestore.connect_book()
        job = get_active_job(st.session_state, db, raw_bytes_list)

    # One live st.status drives the whole pipeline. Frequent label updates send
    # the browser messages that keep the websocket alive during the run (#110).
    with st.status(Uploader.status_header, expanded=True) as status:
        progress = st.progress(0.0)

        if job is not None:
            copyright_text, failed_pages = _finalize_job_batch(
                job, raw_bytes_list, total, fs, db, ai_client, model, status, progress
            )
        elif ai_client is not None:
            copyright_text, failed_pages = _inline_ai_batch(
                raw_bytes_list, total, fs, ai_client, model, status, progress
            )
        else:
            failed_pages = _blank_batch(raw_bytes_list, total, fs, status, progress)

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


def _existing_max_page(fs, current_book):
    """Highest existing page number for ``current_book`` (#203).

    The maximum of the registered ``page_count`` and the highest ``page_N.jpg``
    actually present in the book's S3 folder — so appended pages start beyond
    BOTH, and an existing page file/doc can never be overwritten even if the
    two have drifted.
    """
    page_count = current_book.page_count
    if not isinstance(page_count, int) or page_count < 0:
        page_count = 0
    folder = book_folder_name(current_book.title, current_book.photos_url)
    return max(page_count, max_folder_page(fs, folder))


def append_photo_batch(raw_bytes_list, fs):
    """Process new photos as pages APPENDED after the book's current last page
    (#203) — numbered ``N+1 … N+k`` where N is the existing maximum, through the
    same per-page correction/OCR pipeline as a fresh upload, WITHOUT renumbering
    or rewriting any existing page.

    Reuses ``_inline_ai_batch`` / ``_blank_batch`` with ``start_page=N``; those
    update ``page_count`` to ``N+k`` via the Book write-through. The copyright
    ISBN lookup is deliberately not run here (it seeds the ADD-book form, which
    this existing book has long since left). Returns
    ``(start_page, added, failed_pages)`` on success or ``None`` when aborted by
    the never-overwrite guard.
    """
    current_book = st.session_state['current_book']
    total = len(raw_bytes_list)
    model = get_ai_settings()['extraction_model']
    start_page = _existing_max_page(fs, current_book)
    photos_url = f"sawimages/{current_book.title}"

    # Belt-and-braces never-overwrite guard: _existing_max_page already places
    # start_page beyond every known page, so this only trips on a genuine race
    # (e.g. two sessions appending to the same book simultaneously).
    if fs.exists(f"{photos_url}/page_{start_page + 1}.jpg"):
        st.error(Uploader.append_collision.format(page=start_page + 1))
        return None

    # Invalidate enter-text's cached page images (#199) — the append writes new
    # page_N(.jpg|_cropped|_display) keys, and the book_pages_dict rebuild plus
    # prefetch must not serve stale cache entries.
    st.session_state['_invalidate_image_cache'] = True

    ai_client = get_anthropic_client()
    with st.status(Uploader.status_header, expanded=True) as status:
        progress = st.progress(0.0)
        if ai_client is not None:
            _, failed_pages = _inline_ai_batch(
                raw_bytes_list, total, fs, ai_client, model, status, progress,
                start_page=start_page,
            )
        else:
            failed_pages = _blank_batch(
                raw_bytes_list, total, fs, status, progress, start_page=start_page,
            )

    if ai_client is None:
        st.warning(PhotoUpload.no_api_key)
    return start_page, total, failed_pages


def _finish_append(start_page):
    """Clear the append-flow state and stage enter-text to rebuild its page dict."""
    st.session_state.pop('_append_result', None)
    st.session_state.pop('_appending_photos', None)
    # Force enter-text to rebuild pages 1..new page_count (the dict was built
    # against the old page_count and would hide the appended pages).
    st.session_state.pop('book_pages_dict', None)
    st.session_state['current_page_number'] = start_page + 1


def append_photos_widget():
    """Upload MORE photos for an existing book; they append as new pages after
    the current last page (#203).

    Rendered by ``pages/page_photo_upload.py`` when the user chooses "Add more
    photos". Uses the direct-to-S3 uploader (mobile-safe, #114) under its own
    ``APPEND_FLOW_KEY`` temp prefix with the same manifest block-until-ready +
    force-proceed affordances as the main widget (#199), plus the phone-QR
    hand-off (#143) so the extra photos can be taken on a phone.
    """
    fs = get_s3_filesystem()
    current_book = st.session_state['current_book']

    # Post-processing state: show the outcome + where to go next. Keyed by book
    # id so a stale result from another book can never surface here.
    result = st.session_state.get('_append_result')
    if result and result.get('book_id') == current_book.document_id:
        start_page, added = result['start_page'], result['added']
        st.success(Uploader.append_success.format(
            added=added, first=start_page + 1, last=start_page + added,
        ))
        failed_pages = result.get('failed_pages') or []
        if failed_pages:
            st.warning(PhotoUpload.extraction_partial_fail.format(
                failed=len(failed_pages), total=added,
                pages=", ".join(str(p) for p in failed_pages),
            ))
        col_go, col_back = st.columns(2)
        if col_go.button(Uploader.append_continue_button, width="stretch",
                         key="uploader_append_continue_button"):
            _finish_append(start_page)
            st.switch_page("./pages/enter_text.py")
        if col_back.button(PhotoUpload.back_to_menu_button, width="stretch",
                           key="uploader_append_back_menu_button"):
            _finish_append(start_page)
            st.switch_page("./pages/book_edit_home.py")
        return

    st.write(Uploader.append_instructions.format(count=max(current_book.page_count, 0)))
    session_id = get_upload_session_id(APPEND_FLOW_KEY)
    put_urls = generate_put_urls(APPEND_FLOW_KEY, session_id)
    manifest_url = generate_manifest_put_url(APPEND_FLOW_KEY, session_id)
    st.iframe(build_uploader_html(put_urls, manifest_url), height=460)

    with st.expander(Uploader.append_phone_expander):
        render_go_to_phone(APPEND_FLOW_KEY, session_id)

    process_col, cancel_col = st.columns(2)
    process = process_col.button(Uploader.append_process_button, width="stretch",
                                 key="uploader_append_process_button")
    if cancel_col.button(Uploader.append_cancel_button, width="stretch",
                         key="uploader_append_cancel_button"):
        # Abandon the append: drop the temp prefix and return to the options.
        cleanup_prefix(fs, APPEND_FLOW_KEY, session_id)
        reset_upload_session(APPEND_FLOW_KEY)
        st.session_state.pop('_append_incomplete_count', None)
        st.session_state.pop('_appending_photos', None)
        st.rerun()

    # Block-until-ready + no-dead-end escape hatch (#199), mirroring the main
    # widget: reading is gated on the upload confirming completion (manifest,
    # with the count-stability heuristic as manifest-less fallback).
    incomplete = st.session_state.get('_append_incomplete_count')
    force = False
    if incomplete:
        st.warning(Uploader.upload_incomplete_prompt.format(n=incomplete))
        force = st.button(Uploader.force_process_button,
                          key="uploader_append_force_button")
    if not (process or force):
        return
    if not force:
        with st.spinner(BookPhotoEntry.checking_uploads):
            settled, keys = uploads_settled(fs, APPEND_FLOW_KEY, session_id)
        if keys and not settled:
            st.session_state['_append_incomplete_count'] = len(keys)
            st.rerun()
    st.session_state.pop('_append_incomplete_count', None)

    with st.spinner(BookPhotoEntry.reading_photos):
        pages = fetch_uploaded_photos(fs, APPEND_FLOW_KEY, session_id)
    if not pages:
        st.warning(Uploader.no_photos_uploaded)
        return

    outcome = append_photo_batch([data for _name, data in pages], fs)
    _cleanup_upload_buffer(fs, APPEND_FLOW_KEY)
    if outcome is not None:
        start_page, added, failed_pages = outcome
        st.session_state['_append_result'] = {
            'book_id': current_book.document_id,
            'start_page': start_page,
            'added': added,
            'failed_pages': failed_pages,
        }
        st.rerun()


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
            manifest_url = generate_manifest_put_url(UPLOAD_FLOW_KEY, session_id)
            st.iframe(build_uploader_html(put_urls, manifest_url), height=460)

            process = st.button(Uploader.process_button, key="uploader_process_button")
            if not done:
                # No-dead-end escape hatch (#199): a blocked Process click set
                # _uploader_incomplete_count and reran, so this prompt + the
                # proceed-anyway button persist until resolved.
                incomplete = st.session_state.get('_uploader_incomplete_count')
                force = False
                if incomplete:
                    st.warning(Uploader.upload_incomplete_prompt.format(n=incomplete))
                    force = st.button(
                        Uploader.force_process_button,
                        key="uploader_force_process_button",
                    )
                if not (process or force):
                    return
                if not force:
                    # Gate reading on the upload confirming completion (#199):
                    # primarily the explicit manifest, with the legacy count-
                    # stability heuristic only as a manifest-less fallback.
                    # Reading a PARTIAL concurrent batch here assigned page
                    # numbers positionally over a hole-y listing, permanently
                    # baking a shifted page order into the book.
                    with st.spinner(BookPhotoEntry.checking_uploads):
                        settled, keys = uploads_settled(fs, UPLOAD_FLOW_KEY, session_id)
                    if keys and not settled:
                        st.session_state['_uploader_incomplete_count'] = len(keys)
                        st.rerun()
                st.session_state.pop('_uploader_incomplete_count', None)
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
