"""Append-only debug log for AI page-extraction failures (issue #132).

When the AI page-extraction step fails — a genuine Anthropic API error, or a
reply that cannot be parsed as usable JSON — we must NOT show the archivist a raw
API error and must NOT silently save a blank page. ``ExtractionErrorLog`` records
the full failure detail to a dedicated ``extraction_errors`` Firestore collection
so Chris can review failures later (after the student cohort enters books) and
improve the system, while the user only ever sees a simple "N pages couldn't be
read" summary.

DATA-MODEL CHOICE — deliberate raw-dict writer (NOT ``DataStructureBase``/``Field``)
---------------------------------------------------------------------------------
This mirrors ``EditLog`` (issue #47 / DECISIONS-005), the other documented
raw-dict audit writer, for the same reasons:
  * the record is APPEND-ONLY and IMMUTABLE — written once, never edited
    field-by-field, so the per-field write-through ``Field`` pattern buys nothing;
  * it has NO natural deterministic ``document_id`` — the same (book, page) can
    fail repeatedly over time, so an auto-generated id (Firestore ``add()``) is
    the correct key, not a content-derived one;
  * it is produced by the pipeline, not bound to a ``to_form()`` widget.

Forcing it into ``DataStructureBase`` would mean inventing an artificial
``document_id`` and a no-op ``to_form()``/``form_fields``, so — like ``User`` and
``EditLog`` — it is a deliberate, justified raw-dict writer.

Schema of an ``extraction_errors`` document
-------------------------------------------
  book_id       (str|None)            — the book's document id (``None`` if unknown)
  book_title    (str|None)            — denormalised for human-readable reports
  page_number   (int|None)            — the 1-based page that failed
  page_name     (str|None)            — the page image filename, when known
  error_type    (str)                 — API error class name, ``parse_error``, or
                                         (for a per-page isolation failure, see
                                         ``log_extraction_error``) the raw Python
                                         exception class name (e.g. ``OSError``)
  error_message (str)                 — the real exception / parse-failure text
  username      (str|None)            — who triggered the upload (entered_by)
  flow          (str|None)            — 'single' | 'batch' | 'reconstruction'
  model         (str|None)            — the extraction model configured for this
                                         run, when known (admin-configurable, #135)
  timestamp     (datetime, UTC)       — when the failure was recorded
"""

import logging
from datetime import datetime, timezone

import streamlit as st

logger = logging.getLogger(__name__)


class ExtractionErrorLog:

    COLLECTION = 'extraction_errors'

    # The upload flow a failure came from.
    FLOW_SINGLE = 'single'
    FLOW_BATCH = 'batch'
    FLOW_RECONSTRUCTION = 'reconstruction'

    # A reply that came back but could not be parsed as usable JSON. Genuine
    # Anthropic API errors are recorded with the exception class name as the type.
    ERROR_PARSE = 'parse_error'

    @staticmethod
    def _coerce(value):
        """Coerce a value into something Firestore can store and a human can read.

        Native scalars pass through unchanged; a Firestore ``DocumentReference`` is
        recorded as its ``path`` string; anything else falls back to ``str`` so a
        record can never fail to serialise (mirrors ``EditLog._coerce``).
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        path = getattr(value, 'path', None)
        if path is not None:
            return path
        return str(value)

    @classmethod
    def record(cls, *, book_id, book_title, page_number, error_type,
               error_message, page_name=None, username=None, flow=None,
               model=None):
        """Append one failure record to the ``extraction_errors`` collection.

        The write is fully guarded: a logging failure must NEVER break the upload
        the archivist is running (#132), so any error here is logged and swallowed
        (the deliberate broad catch is justified by that requirement) and ``None``
        is returned. On success returns the new ``DocumentReference``.
        """
        try:
            firestore = st.session_state.get('firestore')
            if firestore is None:
                logger.warning(
                    "ExtractionErrorLog.record: no firestore in session; "
                    "skipping error-log write for book=%s page=%s",
                    book_id, page_number,
                )
                return None
            return firestore.add_document(
                collection=cls.COLLECTION,
                data={
                    'book_id': cls._coerce(book_id),
                    'book_title': book_title,
                    'page_number': page_number,
                    'page_name': page_name,
                    'error_type': error_type,
                    'error_message': str(error_message),
                    'username': cls._coerce(username),
                    'flow': flow,
                    'model': model,
                    'timestamp': datetime.now(timezone.utc),
                },
            )
        except Exception as exc:  # noqa: BLE001 - see docstring: must not break upload
            logger.warning("ExtractionErrorLog.record failed to write: %s", exc)
            return None


def log_extraction_error(*, book=None, page_number=None, page_name=None,
                          error_type, error_message, flow=None, model=None,
                          username=None):
    """Shared entry point for logging ANY page-processing failure (#129, harden-
    page-loop-error-logging).

    Extracts ``book_id``/``book_title`` from a book-like object (mirroring the
    ``getattr(book, 'document_id'/'title', None)`` pattern previously duplicated
    at each call site) and defaults ``username`` from the session, then routes
    through ``ExtractionErrorLog.record``. Two call sites share this:

      * ``pages.uploader.extract_page_info`` — the pre-existing Anthropic-API /
        JSON-parse failure path (#132).
      * ``pages.uploader._process_photo_batch``'s per-page isolation boundary —
        ANY other exception raised while processing one page (a corrupt photo
        hitting OpenCV/PIL, an S3 write blip, a Firestore ``register()`` error),
        so every kind of page failure ends up visible in the same
        ``extraction_errors`` collection, not just API errors.

    Inherits ``record``'s guarantee that a logging failure itself can never
    raise — this function does no extra work that could throw before calling it.
    """
    book_id = getattr(book, 'document_id', None) if book is not None else None
    book_title = getattr(book, 'title', None) if book is not None else None
    if username is None:
        username = st.session_state.get('username')
    return ExtractionErrorLog.record(
        book_id=book_id,
        book_title=book_title,
        page_number=page_number,
        page_name=page_name,
        error_type=error_type,
        error_message=error_message,
        username=username,
        flow=flow,
        model=model,
    )
