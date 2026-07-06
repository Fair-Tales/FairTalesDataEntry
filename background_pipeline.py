"""Background page-processing job for the photo-first book entry flow (#179).

The crop + rotation + OCR (+ whole-book character detection) pipeline is slow —
minutes for a typical book — and previously only started when the user proceeded
past the Add-Book metadata step. This module lets ``pages/add_book_photos.py``
kick that work off in a **daemon worker thread** the moment the photos finish
uploading, so it runs WHILE the user checks the extracted metadata and enters
the author / illustrator / publisher. ``pages.uploader._process_photo_batch``
then recognises the job (by a fingerprint of the photo bytes) and consumes the
precomputed per-page results instead of re-running the slow work.

Threading rules (why this is safe inside Streamlit)
---------------------------------------------------
Streamlit is single-threaded per session and its APIs are not thread-safe, so
the worker is given ONLY plain inputs and touches ONLY plain objects:

* inputs: the raw photo bytes, an ``anthropic`` client (thread-safe), and a
  pre-read **copy** of the AI settings dict — ``get_ai_settings`` (a Streamlit
  cache) is called by the STARTER on the script thread, never by the worker;
* no ``st.session_state`` / ``st.cache_*`` / widget calls, no S3 (the final
  ``sawimages/{title}/…`` destination depends on the book title, which the user
  can still edit on the metadata form), and no session ``FirestoreWrapper``
  (the write-through ``Field``/session-lookup machinery is script-thread only);
* results go into a plain dict created by the starter. ``st.session_state``
  merely holds a reference to that dict; the worker mutates the plain object,
  never the session-state API. Per-page dict item assignment is atomic under
  the GIL, so no lock is needed for the single-producer/single-consumer use.

Everything session-bound — the S3 writes, ``Page.register()``, the
``extraction_errors`` logging — happens at CONSUME time on the script thread in
``uploader._process_photo_batch``. Failed pages recorded by the worker are
logged there through the exact same ``log_extraction_error`` path as the inline
pipeline. Pages the worker never reached (it crashed, or the job was cancelled)
simply fall back to the existing inline processing.

The worker checks the cooperative ``cancelled`` flag between pages, so a
cancelled photo-first entry stops burning AI calls after at most the in-flight
page. Threads are ``daemon`` so they never block interpreter shutdown.
"""

import hashlib
import logging
import threading
import time

from image_processing import correct_page_image, exif_transpose_bytes
from utilities import detect_book_characters

logger = logging.getLogger(__name__)

#: Session-state key holding the (single) in-flight job for this session.
JOB_STATE_KEY = '_background_page_job'

#: Poll interval while the consumer waits for the worker to finish a page.
_POLL_SECONDS = 0.3

#: Usage/flow label for the worker's OCR calls (shows up in the admin usage
#: rollup separately from the inline 'single' flow, so background work is
#: attributable).
_WORKER_EXTRACTION_LABEL = 'background_page_extraction'


def _photos_fingerprint(raw_bytes_list):
    """Cheap, stable fingerprint of one uploaded photo set.

    Hashes the page count plus the first 64KB of the first and last photos —
    enough to tell photo sets apart without hashing tens of MB. Used to match
    the job started at upload time with the photo list handed to the consumer
    (which receives the SAME in-memory ``photo_first_pages`` bytes).
    """
    digest = hashlib.md5()
    digest.update(str(len(raw_bytes_list)).encode())
    if raw_bytes_list:
        digest.update(raw_bytes_list[0][:65536])
        digest.update(raw_bytes_list[-1][:65536])
    return digest.hexdigest()


def _run_job(job, raw_bytes_list, client, settings):
    """Worker body: process every page in order, then detect characters.

    Per-page failures are contained (mirroring ``_process_photo_batch``'s
    isolation boundary): the classified error is recorded in the page's result
    entry and logged to ``extraction_errors`` by the consumer on the script
    thread. A failure here can therefore never blank out the rest of the book.
    """
    # Imported lazily to avoid a module-level cycle: pages.uploader imports the
    # job helpers from this module at import time, while the worker only needs
    # uploader's extraction helper once it is actually running.
    from pages.uploader import attempt_page_extraction

    story_pages = []
    try:
        for i, raw_bytes in enumerate(raw_bytes_list):
            if job['cancelled']:
                return
            page_number = i + 1
            entry = {'corrected': None, 'method': None}
            try:
                oriented = exif_transpose_bytes(raw_bytes)
                bytes_for_extraction, corrected, method = correct_page_image(
                    oriented, client, settings
                )
                entry['corrected'] = corrected
                entry['method'] = method
                entry['extraction'] = attempt_page_extraction(
                    bytes_for_extraction, client, settings,
                    label=_WORKER_EXTRACTION_LABEL,
                )
            except Exception as exc:  # noqa: BLE001 - per-page isolation boundary, see docstring
                logger.warning(
                    "background page job: page %s failed: %s", page_number, exc
                )
                entry['extraction'] = ('error', (type(exc).__name__, str(exc)))
            # Accumulate the story text locally (job['results'] entries are
            # popped by the consumer as it goes, so they cannot be re-read here).
            outcome, payload = entry['extraction']
            if outcome == 'ok':
                text, is_story, _page_type = payload
                if is_story and (text or "").strip():
                    story_pages.append((page_number, text))
            job['results'][page_number] = entry

        if job['cancelled'] or not story_pages:
            return
        # Whole-book character detection (#52/#170) — precomputed here so the
        # enter-text page can show the review form instantly instead of making
        # the user wait through another AI call. Suggestions are review-only;
        # nothing is written until the user confirms them (unchanged).
        try:
            job['character_suggestions'] = detect_book_characters(
                story_pages, client,
                model=settings['character_detection_model'],
            )
        except Exception as exc:  # noqa: BLE001 - optional precompute; consumer falls back to a live run
            logger.warning(
                "background page job: character detection failed "
                "(enter-text will run it live instead): %s", exc,
            )
            job['character_error'] = str(exc)
    finally:
        job['done'] = True


def start_page_processing_job(session_state, raw_bytes_list, client, ai_settings):
    """Start (or reuse) the background processing job for this photo set.

    Idempotent per photo set: a rerun of the starter page finds the matching
    in-flight job and returns it rather than double-processing. Starting a job
    for a DIFFERENT photo set cancels the stale one first.
    """
    fingerprint = _photos_fingerprint(raw_bytes_list)
    existing = session_state.get(JOB_STATE_KEY)
    if existing is not None:
        if existing['fingerprint'] == fingerprint and not existing['cancelled']:
            return existing
        existing['cancelled'] = True

    job = {
        'fingerprint': fingerprint,
        'results': {},
        'total': len(raw_bytes_list),
        'done': False,
        'cancelled': False,
        'character_suggestions': None,
        'character_error': None,
        'settings': dict(ai_settings),
        'thread': None,
    }
    thread = threading.Thread(
        target=_run_job,
        args=(job, list(raw_bytes_list), client, job['settings']),
        name="background-page-processing",
        daemon=True,
    )
    job['thread'] = thread
    session_state[JOB_STATE_KEY] = job
    thread.start()
    logger.info(
        "background page job started: %s page(s), fingerprint=%s",
        job['total'], fingerprint,
    )
    return job


def get_page_processing_job(session_state, raw_bytes_list):
    """Return this session's job if it matches ``raw_bytes_list``, else None."""
    job = session_state.get(JOB_STATE_KEY)
    if (
        job is None
        or job['cancelled']
        or job['fingerprint'] != _photos_fingerprint(raw_bytes_list)
    ):
        return None
    return job


def cancel_page_processing_job(session_state):
    """Cancel and drop this session's job (cooperative — the worker stops after
    the in-flight page). Safe to call when no job exists."""
    job = session_state.pop(JOB_STATE_KEY, None)
    if job is not None:
        job['cancelled'] = True


def clear_page_processing_job(session_state):
    """Drop the finished job from the session once it has been consumed."""
    session_state.pop(JOB_STATE_KEY, None)


def _worker_alive(job):
    thread = job.get('thread')
    return thread is not None and thread.is_alive()


def wait_for_page_result(job, page_number, on_wait=None):
    """Block until the worker has produced (or can no longer produce) the result
    for ``page_number``; pop and return it, or ``None`` when the worker died
    before reaching this page (the caller then falls back to inline processing).

    Popping frees the corrected-image bytes as they are consumed, so peak memory
    is one page's artifacts plus whatever the worker is ahead by. ``on_wait`` is
    invoked once per poll tick so the caller can keep its status UI (and the
    websocket, #110) fed during a long wait.
    """
    while page_number not in job['results']:
        if job['done'] or not _worker_alive(job):
            break
        if on_wait is not None:
            on_wait()
        time.sleep(_POLL_SECONDS)
    return job['results'].pop(page_number, None)


def wait_for_character_suggestions(job, on_wait=None):
    """Block until the worker finishes (it runs character detection last) and
    return the precomputed suggestions, or ``None`` when detection failed or was
    never reached — the enter-text page then falls back to a live run."""
    while not job['done'] and _worker_alive(job):
        if on_wait is not None:
            on_wait()
        time.sleep(_POLL_SECONDS)
    return job.get('character_suggestions')
