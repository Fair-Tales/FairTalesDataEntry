"""Bulk upload page backed by the durable shared extraction pipeline.

The old per-page adapter remains for compatibility with existing callers and
regression coverage. New uploads always use batch_upload_ui and pipeline jobs.
"""
import streamlit as st

from data_structures import Page, ExtractionErrorLog, log_extraction_error
from image_processing import exif_transpose_bytes
from page_results import apply_page_correction
from background_pipeline import _SERVICE_ERRORS
from pages.uploader import (
    _process_page, extract_page_info, _make_reporter, PageExtractionError,
    _register_placeholder_page,
)
from text_content import BatchBookEntry, Uploader
from utilities import (
    page_layout, check_authentication_status, fuzzy_match_name,
    get_anthropic_client, get_ai_settings,
)


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

            # Keep the legacy adapter's page failures isolated and visible.
            # New uploads use durable jobs; this path remains for older callers.
            try:
                bytes_for_extraction, method, rotation_uncertain = _process_page(
                    raw_bytes, page_number, photos_url, fs, ai_client, report
                )

                apply_page_correction(page, corrected=method is not None,
                                      rotation_uncertain=rotation_uncertain)
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
            except _SERVICE_ERRORS as exc:
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
            except _SERVICE_ERRORS as exc:
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


def main():
    check_authentication_status()
    page_layout(current_page="./pages/add_books_batch.py")
    st.header(BatchBookEntry.header)
    from batch_upload_ui import render
    render(get_anthropic_client())


if __name__ == "__main__":
    main()
