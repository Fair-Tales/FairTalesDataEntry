"""Durable background page-processing job for the photo-first book entry flow
(#179).

The crop + rotation + OCR (+ whole-book character detection) pipeline is slow —
minutes for a typical book — and previously only started when the user proceeded
past the Add-Book metadata step. This module kicks that work off the moment the
photos finish uploading so it runs WHILE the user checks the extracted metadata
and enters the author / illustrator / publisher, and ``uploader``'s consume path
finalises whatever is done.

What drives the processing (and the honest limitation)
------------------------------------------------------
Streamlit Cloud has **no separate always-on worker process**. The driver is a
**daemon worker thread spawned by whichever browser session is currently
active**. What makes this robust — and the point of this rewrite — is that the
worker persists everything durably as it goes, so it is *not* the single point
of failure:

* **Job state + per-page results** live in Firestore
  (``pipeline_jobs/{job_id}`` + a ``pages`` subcollection), not an in-RAM dict.
* **Page images** (raw ``page_N.jpg`` + corrected ``page_N_cropped.jpg``) are
  written to **S3 as each page is processed**, not held in memory.

Consequences:

* **Survives a websocket drop / reconnect** — the daemon thread is a process
  thread independent of the websocket, so a brief reconnect never interrupts
  it; and even if the server-side session is lost / the app is redeployed /
  the process restarts (Streamlit Cloud sleeps and redeploys), the durable job
  doc + already-written S3 images mean **no work is lost and none is redone** —
  a later session's worker resumes from the durable state, processing only the
  pages still marked pending (page-level lease claim, below).
* **Flat memory footprint** — images live in S3, results in Firestore; the
  process holds at most one page's bytes at a time.

**The honest gap vs a true external worker:** because the driver is a thread
inside an active session, if the user **fully closes the tab with no session
active anywhere**, in-flight processing *pauses* — the job doc and the S3
images that were already written persist, and processing *resumes from where it
left off* the next time any session for that job runs the pipeline, but it does
not advance while nobody is connected. A true external worker (e.g. a Cloud Run
queue consumer) would keep going with the tab closed; that is out of scope on
Streamlit Cloud, which has no place to run one.

Thread-safety
-------------
The worker is handed only **plain, thread-safe objects**, all constructed on the
script thread by the starter/consumer and passed in: the app's ``s3fs``
filesystem (``get_s3_filesystem()``), a raw ``google.cloud.firestore`` client
(``FirestoreWrapper.connect_book()``), an ``anthropic`` client, and a **copy**
of the validated AI settings dict. The worker NEVER touches ``st.session_state``
/ ``st.cache_*`` / widgets, nor the session ``FirestoreWrapper`` (whose
``connect_book`` reads/writes ``st.session_state`` via ``is_authenticated``).
All ``Page.register()`` write-through work and ``extraction_errors`` logging
stay on the script thread in the consumer.
"""

import hashlib
import logging
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from google.cloud import firestore

from image_processing import correct_page_image, exif_transpose_bytes, make_display_copy
from s3_constants import S3_BUCKET
from utilities import detect_book_characters

logger = logging.getLogger(__name__)

#: Firestore collection of durable job records (parent docs). Each carries a
#: ``pages`` subcollection with one doc per page number.
PIPELINE_JOBS_COLLECTION = 'pipeline_jobs'

#: Session-state key holding this session's link to its job:
#: ``{'job_id', 'fingerprint', 's3_prefix', 'working_title'}``.
JOB_STATE_KEY = '_background_page_job'

#: Poll interval while a consumer waits on a not-yet-finished page.
_POLL_SECONDS = 0.4

#: How long a page's ``processing`` claim is honoured before another worker may
#: re-claim it. Long enough to cover one slow page's model calls; short enough
#: that a dead worker's page is picked up promptly on resume.
_CLAIM_LEASE_SECONDS = 180

#: Usage/flow label for the worker's OCR calls, so background work is
#: attributable separately in the admin usage rollup.
_WORKER_EXTRACTION_LABEL = 'background_page_extraction'

#: In-process registry of workers running in THIS process, keyed by job_id:
#: ``{job_id: {'thread': Thread, 'cancel': threading.Event}}``. Lets us avoid
#: double-spawning, signal cancellation, and know whether a worker is alive
#: locally (a resuming session in a different process finds an empty registry
#: and spawns its own).
_ACTIVE_WORKERS = {}
_REGISTRY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _photos_fingerprint(raw_bytes_list):
    """Cheap, stable fingerprint of one uploaded photo set — hashes the page
    count plus the first 64KB of the first and last photos, so the consumer can
    confirm the session's job matches the photos it was handed without hashing
    tens of MB."""
    digest = hashlib.md5()
    digest.update(str(len(raw_bytes_list)).encode())
    if raw_bytes_list:
        digest.update(raw_bytes_list[0][:65536])
        digest.update(raw_bytes_list[-1][:65536])
    return digest.hexdigest()


def _placeholder_title(job_id):
    """S3 working folder name used until the real book title is known / when the
    extracted title would collide with an existing book's folder. Namespaced by
    job id so concurrent entries never collide, and prefixed so the cleanup CLI
    can recognise it as transient (mirrors the ``uploads/`` convention)."""
    return f"__pending__{job_id}"


def _s3_prefix_for(working_title):
    return f"{S3_BUCKET}/{working_title}"


def _s3_write(fs, path, data):
    with fs.open(path, 'wb') as f:
        f.write(data)


def _s3_read(fs, path):
    with fs.open(path, 'rb') as f:
        return f.read()


def _job_ref(db, job_id):
    return db.collection(PIPELINE_JOBS_COLLECTION).document(job_id)


def _page_ref(db, job_id, page_number):
    return _job_ref(db, job_id).collection('pages').document(str(page_number))


# ---------------------------------------------------------------------------
# Result (de)serialisation — the worker persists a page's outcome; the consumer
# reads it back into the ('ok', (...)) / ('error', (...)) shape that
# uploader.attempt_page_extraction produces, so the consume path stays uniform.
# ---------------------------------------------------------------------------

def _result_to_fields(outcome, payload, method, corrected):
    common = {'method': method, 'corrected': bool(corrected), 'updated_at': _now()}
    if outcome == 'ok':
        text, is_story, page_type = payload
        common.update(
            status='done', text=text, is_story=bool(is_story), page_type=page_type,
        )
    else:
        error_type, error_message = payload
        common.update(
            status='failed', error_type=error_type, error_message=error_message,
        )
    return common


def _fields_to_result(data):
    """Reconstruct ``(outcome, payload, method, corrected)`` from a page doc, or
    ``None`` when the page has no terminal result yet."""
    status = data.get('status')
    if status == 'done':
        return (
            'ok',
            (data.get('text', ''), bool(data.get('is_story', False)), data.get('page_type', '')),
            data.get('method'),
            bool(data.get('corrected', False)),
        )
    if status == 'failed':
        return (
            'error',
            (data.get('error_type', 'Error'), data.get('error_message', '')),
            data.get('method'),
            bool(data.get('corrected', False)),
        )
    return None


# ---------------------------------------------------------------------------
# Page-level claim (lease) — a transaction so two concurrent workers (e.g. a
# resumed session overlapping a still-live one) never both pay for the same
# page's AI calls. A lost claim just skips the page; a page whose claim lease
# has expired (dead worker) is re-claimable.
# ---------------------------------------------------------------------------

def _should_claim(data, now=None):
    """Pure claim decision (kept separate so it is unit-testable without the
    transaction machinery): claim a page that has no doc yet, is still pending,
    or whose ``processing`` lease has expired; never re-claim a terminal
    (done/failed) page or one under a fresh lease."""
    if data is None:
        return True
    status = data.get('status')
    if status in ('done', 'failed'):
        return False
    if status == 'processing':
        claimed_at = data.get('claimed_at')
        cutoff = (now or _now()) - timedelta(seconds=_CLAIM_LEASE_SECONDS)
        if claimed_at is not None and claimed_at > cutoff:
            return False  # fresh lease held by another worker
    return True


def _claim_page(db, job_id, page_number, worker_id):
    page_ref = _page_ref(db, job_id, page_number)
    transaction = db.transaction()

    @firestore.transactional
    def _txn(transaction):
        snap = page_ref.get(transaction=transaction)
        data = snap.to_dict() if snap.exists else None
        if not _should_claim(data):
            return False
        transaction.set(
            page_ref,
            {
                'page_number': page_number,
                'status': 'processing',
                'claimed_by': worker_id,
                'claimed_at': _now(),
            },
            merge=True,
        )
        return True

    return _txn(transaction)


# ---------------------------------------------------------------------------
# Worker body.
# ---------------------------------------------------------------------------

def _run_worker(fs, db, client, settings, job_id, s3_prefix, page_count,
                raw_bytes_list, cancel_event):
    """Process the job durably: raw upload → per-page correction/OCR (each
    persisted to S3 + Firestore) → whole-book character detection.

    ``raw_bytes_list`` may be ``None`` on a resume with no in-memory photos —
    the per-page phase then reads each raw ``page_N.jpg`` back from S3 (written
    by the raw-upload phase, which only runs when bytes ARE available). Every
    per-page failure is contained and recorded as a ``failed`` result so a
    single bad page can never abort the rest.
    """
    from pages.uploader import attempt_page_extraction

    worker_id = uuid.uuid4().hex
    job_ref = _job_ref(db, job_id)

    def _cancelled():
        if cancel_event.is_set():
            return True
        snap = job_ref.get()
        return (not snap.exists) or (snap.to_dict() or {}).get('status') == 'cancelled'

    try:
        # Phase A — durable raw upload (only when we hold the bytes and it has
        # not already run). Makes every raw page durable in S3 so a later resume
        # (which may have no in-memory bytes) can still finish the per-page work.
        snap = job_ref.get()
        raw_uploaded = bool((snap.to_dict() or {}).get('raw_uploaded')) if snap.exists else False
        if not raw_uploaded and raw_bytes_list is not None:
            for i, raw_bytes in enumerate(raw_bytes_list):
                if _cancelled():
                    return
                _s3_write(fs, f"{s3_prefix}/page_{i + 1}.jpg", exif_transpose_bytes(raw_bytes))
            job_ref.set({'raw_uploaded': True, 'updated_at': _now()}, merge=True)

        # Phase B — per-page correction + OCR, each result persisted immediately.
        for page_number in range(1, page_count + 1):
            if _cancelled():
                return
            if not _claim_page(db, job_id, page_number, worker_id):
                continue  # already done, or fresh-claimed by another worker
            page_ref = _page_ref(db, job_id, page_number)
            try:
                if raw_bytes_list is not None:
                    raw_bytes = raw_bytes_list[page_number - 1]
                else:
                    raw_bytes = _s3_read(fs, f"{s3_prefix}/page_{page_number}.jpg")
                oriented = exif_transpose_bytes(raw_bytes)  # idempotent
                bytes_for_extraction, corrected, method = correct_page_image(
                    oriented, client, settings
                )
                if corrected is not None:
                    _s3_write(fs, f"{s3_prefix}/page_{page_number}_cropped.jpg", corrected)
                # Screen-sized display derivative (#184): enter-text ships this
                # instead of the multi-MB original. Derive it from the corrected
                # image when one exists (that is what enter-text shows by default),
                # else the oriented raw.
                _s3_write(
                    fs, f"{s3_prefix}/page_{page_number}_display.jpg",
                    make_display_copy(corrected if corrected is not None else oriented),
                )
                outcome, payload = attempt_page_extraction(
                    bytes_for_extraction, client, settings, label=_WORKER_EXTRACTION_LABEL,
                )
                page_ref.set(_result_to_fields(outcome, payload, method, corrected is not None), merge=True)
            except Exception as exc:  # noqa: BLE001 - per-page isolation boundary, see docstring
                logger.warning("background job %s: page %s failed: %s", job_id, page_number, exc)
                page_ref.set(
                    _result_to_fields('error', (type(exc).__name__, str(exc)), None, False),
                    merge=True,
                )

        if _cancelled():
            return

        # Phase C — whole-book character detection over the persisted story
        # text, so enter-text can show the review form without a further AI
        # call. Only when it has not already been done for this job.
        snap = job_ref.get()
        job_data = snap.to_dict() or {}
        if job_data.get('character_status') not in ('done', 'failed'):
            _detect_characters(db, job_id, client, settings)

        job_ref.set({'status': 'complete', 'updated_at': _now()}, merge=True)
    except Exception as exc:  # noqa: BLE001 - worker top-level guard: never crash the thread silently
        logger.warning("background job %s: worker aborted: %s", job_id, exc)
    finally:
        with _REGISTRY_LOCK:
            entry = _ACTIVE_WORKERS.get(job_id)
            if entry is not None and entry.get('thread') is threading.current_thread():
                _ACTIVE_WORKERS.pop(job_id, None)


def _detect_characters(db, job_id, client, settings):
    """Run character detection over the job's persisted story pages and store
    the suggestions on the parent doc. Failure is recorded (not raised) so the
    consumer falls back to a live detection run."""
    job_ref = _job_ref(db, job_id)
    story_pages = []
    for page_snap in job_ref.collection('pages').stream():
        data = page_snap.to_dict() or {}
        if data.get('status') == 'done' and data.get('is_story') and (data.get('text') or '').strip():
            story_pages.append((int(data.get('page_number', page_snap.id)), data['text']))
    story_pages.sort()
    if not story_pages:
        job_ref.set({'character_status': 'skipped', 'updated_at': _now()}, merge=True)
        return
    try:
        suggestions = detect_book_characters(
            story_pages, client, model=settings['character_detection_model'],
        )
        job_ref.set(
            {'character_status': 'done', 'character_suggestions': suggestions, 'updated_at': _now()},
            merge=True,
        )
    except Exception as exc:  # noqa: BLE001 - optional precompute; consumer runs it live instead
        logger.warning("background job %s: character detection failed: %s", job_id, exc)
        job_ref.set(
            {'character_status': 'failed', 'character_error': str(exc), 'updated_at': _now()},
            merge=True,
        )


# ---------------------------------------------------------------------------
# Worker lifecycle (spawn / resume / cancel).
# ---------------------------------------------------------------------------

def _spawn_worker(fs, db, client, settings, job_id, s3_prefix, page_count, raw_bytes_list):
    """Start a daemon worker for ``job_id`` in this process if one is not already
    running here. Returns the (new or existing) registry entry."""
    with _REGISTRY_LOCK:
        entry = _ACTIVE_WORKERS.get(job_id)
        if entry is not None and entry['thread'].is_alive():
            return entry
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=_run_worker,
            args=(fs, db, client, dict(settings), job_id, s3_prefix, page_count,
                  list(raw_bytes_list) if raw_bytes_list is not None else None,
                  cancel_event),
            name=f"background-page-job-{job_id}",
            daemon=True,
        )
        entry = {'thread': thread, 'cancel': cancel_event}
        _ACTIVE_WORKERS[job_id] = entry
        thread.start()
    return entry


def start_page_processing_job(session_state, fs, db, client, ai_settings,
                              raw_bytes_list, *, entered_by, extracted_title):
    """Create (or reuse) the durable job for this photo set and start its worker.

    Idempotent per photo set within a session: a rerun of the starter page finds
    the session's matching job and just ensures its worker is alive rather than
    creating a second job. Returns the session job link dict.

    ``fs``/``db``/``client`` are built on the SCRIPT thread by the caller and
    handed to the worker (see module docstring on thread-safety). ``db`` is a
    raw ``firestore`` client (``FirestoreWrapper.connect_book()``).
    ``extracted_title`` is the AI-extracted book title if metadata extraction
    produced one, else falsy — it seeds the S3 working folder so the common
    "user keeps the extracted title" case needs no rename at finalize.
    """
    fingerprint = _photos_fingerprint(raw_bytes_list)
    page_count = len(raw_bytes_list)

    existing = session_state.get(JOB_STATE_KEY)
    if existing is not None and existing.get('fingerprint') == fingerprint:
        _spawn_worker(fs, db, client, ai_settings, existing['job_id'],
                      existing['s3_prefix'], page_count, raw_bytes_list)
        return existing

    job_id = uuid.uuid4().hex

    # Choose the S3 working folder: the extracted title when it is usable and
    # would NOT write into an existing book's folder; otherwise a per-job
    # placeholder. Either way the folder is renamed to the final book title at
    # finalize if they differ (#179 title-edit reconciliation).
    working_title = _placeholder_title(job_id)
    title_source = 'placeholder'
    candidate = (extracted_title or '').strip()
    if candidate:
        try:
            collides = fs.exists(f"{S3_BUCKET}/{candidate}/page_1.jpg")
        except Exception as exc:  # noqa: BLE001 - existence probe must never block job start
            logger.warning("background job %s: title-collision probe failed: %s", job_id, exc)
            collides = True
        if not collides:
            working_title = candidate
            title_source = 'extracted'

    s3_prefix = _s3_prefix_for(working_title)

    _job_ref(db, job_id).set({
        'job_id': job_id,
        'entered_by': entered_by,
        'fingerprint': fingerprint,
        'page_count': page_count,
        'working_title': working_title,
        'title_source': title_source,
        'extracted_title': candidate or None,
        's3_prefix': s3_prefix,
        'raw_uploaded': False,
        'status': 'processing',
        'character_status': 'pending',
        'character_suggestions': None,
        'book_id': None,
        'created_at': _now(),
        'updated_at': _now(),
    })

    link = {
        'job_id': job_id,
        'fingerprint': fingerprint,
        's3_prefix': s3_prefix,
        'working_title': working_title,
    }
    session_state[JOB_STATE_KEY] = link
    _spawn_worker(fs, db, client, ai_settings, job_id, s3_prefix, page_count, raw_bytes_list)
    logger.info("background job %s started: %s page(s), prefix=%s", job_id, page_count, s3_prefix)
    return link


def get_active_job(session_state, db, raw_bytes_list):
    """Return this session's job link if it matches ``raw_bytes_list`` and the
    durable job is still live (not cancelled/finalized), else ``None``.

    Reads the job doc so a job cancelled/finalized in another session (or a
    stale session link) is not consumed."""
    link = session_state.get(JOB_STATE_KEY)
    if link is None or link.get('fingerprint') != _photos_fingerprint(raw_bytes_list):
        return None
    snap = _job_ref(db, link['job_id']).get()
    if not snap.exists:
        return None
    status = (snap.to_dict() or {}).get('status')
    if status in ('cancelled', 'finalized'):
        return None
    return link


def ensure_worker_running(fs, db, client, ai_settings, link, raw_bytes_list):
    """Guarantee a worker is advancing this job in the current process — spawns
    one if the original session's worker isn't alive here (resume). A no-op when
    the durable job has already completed (every page terminal + detection done),
    so the common "worker finished during the metadata step" case does not spawn
    a redundant thread."""
    snap = _job_ref(db, link['job_id']).get()
    if snap.exists and (snap.to_dict() or {}).get('status') == 'complete':
        return
    _spawn_worker(fs, db, client, ai_settings, link['job_id'], link['s3_prefix'],
                  len(raw_bytes_list), raw_bytes_list)


def stamp_book_id(db, link, book_id):
    """Record the book id on the job doc once it is known (at consume), so a
    fresh session that lost its in-memory link could rediscover the job by book
    id. Best-effort."""
    try:
        _job_ref(db, link['job_id']).set({'book_id': book_id, 'updated_at': _now()}, merge=True)
    except Exception as exc:  # noqa: BLE001 - non-critical linkage write
        logger.warning("background job %s: could not stamp book_id: %s", link['job_id'], exc)


def wait_for_page_result(db, link, page_number, *, worker_alive, on_wait=None):
    """Block until page ``page_number`` reaches a terminal result and return
    ``(outcome, payload, method, corrected)``, or ``None`` when no worker is
    advancing the job and the page is still unfinished (the consumer then falls
    back to inline processing for that page).

    ``worker_alive`` is a zero-arg callable reporting whether a worker is still
    running in this process; ``on_wait`` is called once per poll tick so the
    caller keeps its status UI (and the websocket, #110) alive."""
    page_ref = _page_ref(db, link['job_id'], page_number)
    while True:
        snap = page_ref.get()
        if snap.exists:
            result = _fields_to_result(snap.to_dict() or {})
            if result is not None:
                return result
        if not worker_alive():
            # Give the just-finished worker a beat to flush its last write, then
            # re-check once before conceding to the inline fallback.
            time.sleep(_POLL_SECONDS)
            snap = page_ref.get()
            if snap.exists:
                result = _fields_to_result(snap.to_dict() or {})
                if result is not None:
                    return result
            return None
        if on_wait is not None:
            on_wait()
        time.sleep(_POLL_SECONDS)


def wait_for_character_suggestions(db, link, *, worker_alive, on_wait=None):
    """Block until character detection reaches a terminal state and return the
    precomputed suggestions, or ``None`` (detection failed / skipped / no worker
    left) so the consumer can run it live instead."""
    job_ref = _job_ref(db, link['job_id'])
    while True:
        data = job_ref.get().to_dict() or {}
        char_status = data.get('character_status')
        if char_status == 'done':
            return data.get('character_suggestions')
        if char_status in ('failed', 'skipped'):
            return None
        if not worker_alive():
            time.sleep(_POLL_SECONDS)
            data = job_ref.get().to_dict() or {}
            if data.get('character_status') == 'done':
                return data.get('character_suggestions')
            return None
        if on_wait is not None:
            on_wait()
        time.sleep(_POLL_SECONDS)


def worker_alive_for(link):
    """Return a zero-arg callable reporting whether a worker for this job is
    alive in the current process (for the wait_* bailout gates)."""
    job_id = link['job_id']

    def _alive():
        with _REGISTRY_LOCK:
            entry = _ACTIVE_WORKERS.get(job_id)
        return entry is not None and entry['thread'].is_alive()

    return _alive


def reconcile_s3_prefix(fs, link, final_title):
    """Move the job's working S3 folder to the final book-title folder if they
    differ (#179 — the user may have edited the title on the metadata form).

    Called at finalize, AFTER every page has reached a terminal result (so the
    worker is no longer writing into the working prefix), and BEFORE the consumer
    writes any inline-fallback images — so both worker-written and fallback pages
    end up under the final title. Returns the final ``sawimages/{title}`` prefix.
    """
    final_prefix = _s3_prefix_for(final_title)
    working_prefix = link['s3_prefix']
    if working_prefix == final_prefix:
        return final_prefix
    try:
        if not fs.exists(working_prefix):
            return final_prefix  # nothing was written yet (all pages fell back)
        for key in fs.ls(working_prefix, detail=False):
            name = key.rsplit('/', 1)[-1]
            fs.mv(key, f"{final_prefix}/{name}")  # server-side copy + delete
        # Drop the now-empty working-prefix marker, if the store keeps one.
        if fs.exists(working_prefix):
            fs.rm(working_prefix, recursive=True)
    except Exception as exc:  # noqa: BLE001 - reconciliation must not abort finalize
        logger.warning("background job %s: S3 reconcile %s -> %s failed: %s",
                       link['job_id'], working_prefix, final_prefix, exc)
    link['s3_prefix'] = final_prefix
    return final_prefix


def finalize_job(db, link, final_title):
    """Mark the job finalized and record its final S3 location. Best-effort."""
    try:
        _job_ref(db, link['job_id']).set(
            {'status': 'finalized', 'final_title': final_title,
             's3_prefix': _s3_prefix_for(final_title), 'updated_at': _now()},
            merge=True,
        )
    except Exception as exc:  # noqa: BLE001 - non-critical status write
        logger.warning("background job %s: could not mark finalized: %s", link['job_id'], exc)


def cancel_page_processing_job(session_state, db=None):
    """Cancel this session's job: signal the in-process worker to stop and mark
    the durable job cancelled so any other/resuming worker stops too. Safe when
    no job exists. ``db`` may be ``None`` (session cleanup without a client) —
    the in-process signal still fires and the doc is left for TTL cleanup."""
    link = session_state.pop(JOB_STATE_KEY, None)
    if link is None:
        return
    with _REGISTRY_LOCK:
        entry = _ACTIVE_WORKERS.get(link['job_id'])
    if entry is not None:
        entry['cancel'].set()
    if db is not None:
        try:
            _job_ref(db, link['job_id']).set(
                {'status': 'cancelled', 'updated_at': _now()}, merge=True
            )
        except Exception as exc:  # noqa: BLE001 - best-effort cancel
            logger.warning("background job %s: could not mark cancelled: %s", link['job_id'], exc)


def clear_page_processing_job(session_state):
    """Drop the finished job link from the session once it has been consumed."""
    session_state.pop(JOB_STATE_KEY, None)
