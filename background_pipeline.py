"""Durable, bounded book extraction using the application's shared helpers.

One daemon scheduler per application process advances one book at a time. The
queue contains job identifiers and explicit clients, never image lists. Raw
sources, settings, page results and character suggestions survive browser and
process loss. Closing a browser does not stop a daemon; application shutdown
can. Resuming after shutdown requires opening the batch again.

Entity persistence stays on the script thread. Operational jobs and claims use
raw-client transactions; worker helpers receive a thread-local usage recorder.
Leases reduce duplicate paid calls but cannot promise exactly-once billing if a
process dies after a provider response and before its durable result commit.
"""

import hashlib
import logging
import json
from collections import deque
from contextlib import contextmanager
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from google.cloud import firestore
from google.api_core.exceptions import GoogleAPIError
from anthropic import AnthropicError
from botocore.exceptions import BotoCoreError, ClientError
from cv2 import error as OpenCVError

from pipeline_worker_runtime import worker_runtime

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
_REGISTRY_LOCK = threading.RLock()
_ADAPTER_LOCK = threading.RLock()
_WORK_QUEUE = deque()
_SCHEDULER_THREAD = None
PROCESSING_VERSION = 'batch-full-v1'
_CHARACTER_TERMINAL = ('done', 'failed', 'skipped')
_SERVICE_ERRORS = (GoogleAPIError, AnthropicError, BotoCoreError, ClientError,
                   OpenCVError, OSError, ValueError, TypeError, KeyError, RuntimeError)


class _DirectTransaction:
    """Serial transaction adapter for minimal in-memory storage implementations.

    Production Firestore clients always take the real transactional branch.
    """
    def set(self, reference, fields, merge=False):
        reference.set(fields, merge=merge)


def _atomic(db, callback):
    factory = getattr(db, 'transaction', None)
    if callable(factory):
        return firestore.transactional(callback)(factory())
    with _ADAPTER_LOCK:
        return callback(_DirectTransaction())



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

def _result_to_fields(outcome, payload, method, corrected, rotation_uncertain=False):
    common = {
        'method': method,
        'corrected': bool(corrected),
        # Orientation check could not be trusted for this page (#217) — carried
        # through to the Page doc by the consumer so it can be surfaced.
        'rotation_uncertain': bool(rotation_uncertain),
        'updated_at': _now(),
    }
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
    """Reconstruct ``(outcome, payload, method, corrected, rotation_uncertain)``
    from a page doc, or ``None`` when the page has no terminal result yet.
    ``rotation_uncertain`` defaults False for results persisted before #217."""
    status = data.get('status')
    if status == 'done':
        return (
            'ok',
            (data.get('text', ''), bool(data.get('is_story', False)), data.get('page_type', '')),
            data.get('method'),
            bool(data.get('corrected', False)),
            bool(data.get('rotation_uncertain', False)),
        )
    if status == 'failed':
        return (
            'error',
            (data.get('error_type', 'Error'), data.get('error_message', '')),
            data.get('method'),
            bool(data.get('corrected', False)),
            bool(data.get('rotation_uncertain', False)),
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

    def claim(transaction):
        job = _job_ref(db, job_id).get(transaction=transaction).to_dict() or {}
        snap = page_ref.get(transaction=transaction)
        data = snap.to_dict() if snap.exists else None
        if job.get('status') in ('cancelled', 'finalized') or not _should_claim(data):
            return False
        transaction.set(page_ref, {
            'page_number': page_number, 'status': 'processing',
            'claimed_by': worker_id, 'claimed_at': _now(),
        }, merge=True)
        return True
    return _atomic(db, claim)


def _owned_page_write(db, job_id, page_number, worker_id, fields):
    """Discard results from expired owners or cancelled jobs atomically."""
    page_ref = _page_ref(db, job_id, page_number)

    def commit(transaction):
        job = _job_ref(db, job_id).get(transaction=transaction).to_dict() or {}
        page = page_ref.get(transaction=transaction).to_dict() or {}
        # Minimal legacy test adapters did not record an owner; real claims do.
        owner = page.get('claimed_by')
        if (job.get('status') in ('cancelled', 'finalized')
                or page.get('status') != 'processing'
                or (owner != worker_id and (owner is not None or callable(getattr(db, 'transaction', None))))):
            return False
        transaction.set(page_ref, fields, merge=True)
        if fields.get('status') in ('done', 'failed'):
            metric = 'pages_done' if fields['status'] == 'done' else 'pages_failed'
            transaction.set(_job_ref(db, job_id), {
                metric: int(job.get(metric, 0)) + 1, 'updated_at': _now(),
            }, merge=True)
        return True
    return _atomic(db, commit)


@contextmanager
def _renewing_lease(renew):
    """Keep paid calls covered by a lease; stale owners cannot renew or commit."""
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(_CLAIM_LEASE_SECONDS / 3):
            try:
                if not renew():
                    return
            except _SERVICE_ERRORS as exc:
                logger.warning('pipeline lease renewal failed: %s', exc)
                return
    thread = threading.Thread(target=heartbeat, name='pipeline-lease', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def _page_docs(db, job_id, page_count):
    return [dict((_page_ref(db, job_id, number).get().to_dict() or {}),
                 page_number=number)
            for number in range(1, page_count + 1)]


def _text_revision(page_docs):
    """Content revision includes failed coverage as well as ordered story text."""
    payload = [[p.get('page_number'), p.get('status'), p.get('text', ''),
                bool(p.get('is_story'))] for p in page_docs]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _character_write(db, job_id, worker_id, revision, fields):
    ref = _job_ref(db, job_id)

    def commit(transaction):
        job = ref.get(transaction=transaction).to_dict() or {}
        if (job.get('status') in ('cancelled', 'finalized')
                or job.get('character_status') != 'processing'
                or job.get('character_claimed_by') != worker_id
                or job.get('character_input_revision') != revision):
            return False
        pages = []
        for number in range(1, job.get('page_count', 0) + 1):
            data = _page_ref(db, job_id, number).get(transaction=transaction).to_dict() or {}
            pages.append(dict(data, page_number=number))
        if _text_revision(pages) != revision:
            return False
        transaction.set(ref, fields, merge=True)
        return True
    return _atomic(db, commit)


# ---------------------------------------------------------------------------
# Worker body.
# ---------------------------------------------------------------------------

def _stage_sources(fs, db, job_id, prefix, page_count, raw_bytes_list, cancelled):
    """Checkpoint raw sources individually; absent input blocks, never blanks OCR."""
    for number in range(1, page_count + 1):
        if cancelled():
            return False
        page_ref = _page_ref(db, job_id, number)
        page = page_ref.get().to_dict() or {}
        destination = f'{prefix}/page_{number}.jpg'
        try:
            if not fs.exists(destination):
                if raw_bytes_list is not None:
                    if number > len(raw_bytes_list):
                        raise ValueError('Missing raw upload slot')
                    raw = raw_bytes_list[number - 1]
                else:
                    source = page.get('source_key')
                    if not source:
                        raise FileNotFoundError(destination)
                    raw = _s3_read(fs, source)
                _s3_write(fs, destination, exif_transpose_bytes(raw))
            page_ref.set({'page_number': number, 'raw_staged': True,
                          'staging_error': None}, merge=True)
            _set_job_if_active(db, job_id, {'pages_staged': number})
        except _SERVICE_ERRORS as exc:
            logger.warning('background job %s: source %s unavailable: %s', job_id, number, exc)
            page_ref.set({'page_number': number, 'raw_staged': False,
                          'staging_error': str(exc)}, merge=True)
            _set_job_if_active(db, job_id, {'status': 'blocked', 'staging_error': str(exc),
                                           'updated_at': _now()})
            return False
    if cancelled():
        return False
    return _set_job_if_active(db, job_id, {
        'raw_uploaded': True, 'staging_error': None, 'updated_at': _now(),
    })


def _publish_page_artifacts(fs, db, job_id, prefix, number):
    """Publish only the winning claim's images; stale attempts stay unreferenced.

    A crash after committing OCR but before publishing images resumes this
    cheap step without repeating extraction. The job cannot complete until the
    canonical images required by existing consumers are present.
    """
    page = _page_ref(db, job_id, number).get().to_dict() or {}
    if page.get('status') not in ('done', 'failed'):
        return
    for field, suffix in (('corrected_source_key', 'cropped'), ('display_source_key', 'display')):
        source = page.get(field)
        target = f'{prefix}/page_{number}_{suffix}.jpg'
        if source and source != target:
            _s3_write(fs, target, _s3_read(fs, source))
        elif (suffix == 'cropped' and page.get('corrected')
              and page.get('artifacts_pending') and not fs.exists(target)):
            raise FileNotFoundError(target)
    def acknowledge(transaction):
        job = _job_ref(db, job_id).get(transaction=transaction).to_dict() or {}
        current = _page_ref(db, job_id, number).get(transaction=transaction).to_dict() or {}
        if (job.get('status') in ('cancelled', 'finalized')
                or current.get('status') != page.get('status')
                or any(current.get(key) != page.get(key)
                       for key in ('corrected_source_key', 'display_source_key'))):
            return
        transaction.set(_page_ref(db, job_id, number), {'artifacts_pending': False}, merge=True)
    _atomic(db, acknowledge)


def _run_worker(fs, db, client, settings, job_id, s3_prefix, page_count,
                raw_bytes_list, cancel_event):
    """Advance one durable book; stage failures and page failures remain visible."""
    # This compatibility wrapper calls extraction_core internally; keeping its
    # injectable boundary preserves the existing single-upload test contract.
    from pages.uploader import attempt_page_extraction

    worker_id = uuid.uuid4().hex
    job_ref = _job_ref(db, job_id)

    def cancelled():
        if cancel_event.is_set():
            return True
        snap = job_ref.get()
        return not snap.exists or (snap.to_dict() or {}).get('status') in ('cancelled', 'finalized')

    try:
        if cancelled():
            return
        if not _stage_sources(fs, db, job_id, s3_prefix, page_count, raw_bytes_list, cancelled):
            return
        job_data = job_ref.get().to_dict() or {}
        if not _set_job_if_active(db, job_id, {'status': 'processing', 'updated_at': _now()}):
            return
        with worker_runtime(client, settings, db, job_id, job_data.get('batch_id')) as worker_client:
            for page_number in range(1, page_count + 1):
                if cancelled():
                    return
                if not _claim_page(db, job_id, page_number, worker_id):
                    _publish_page_artifacts(fs, db, job_id, s3_prefix, page_number)
                    continue
                method, corrected, rotation_uncertain = None, None, False
                artifacts = {}
                try:
                    with _renewing_lease(lambda: _owned_page_write(
                        db, job_id, page_number, worker_id, {'claimed_at': _now()},
                    )):
                        raw_bytes = _s3_read(fs, f'{s3_prefix}/page_{page_number}.jpg')
                        oriented = exif_transpose_bytes(raw_bytes)
                        image, corrected, method, rotation_uncertain = correct_page_image(
                            oriented, worker_client, settings,
                        )
                        if cancelled():
                            return
                        attempt_prefix = f'{s3_prefix}/_attempt_{page_number}_{worker_id}'
                        if corrected is not None:
                            path = f'{attempt_prefix}_cropped.jpg'
                            _s3_write(fs, path, corrected)
                            artifacts['corrected_source_key'] = path
                        path = f'{attempt_prefix}_display.jpg'
                        _s3_write(fs, path, make_display_copy(corrected if corrected is not None else oriented))
                        artifacts['display_source_key'] = path
                        outcome, payload = attempt_page_extraction(
                            image, worker_client, settings, label=_WORKER_EXTRACTION_LABEL,
                        )
                        if cancelled():
                            return
                        fields = _result_to_fields(outcome, payload, method,
                                                   corrected is not None, rotation_uncertain)
                        _owned_page_write(db, job_id, page_number, worker_id, {**fields, **artifacts, 'artifacts_pending': True})
                except _SERVICE_ERRORS as exc:
                    logger.warning('background job %s: page %s failed: %s', job_id, page_number, exc)
                    if not cancelled():
                        fields = _result_to_fields('error', (type(exc).__name__, str(exc)),
                                                   method, corrected is not None, rotation_uncertain)
                        _owned_page_write(db, job_id, page_number, worker_id, {**fields, **artifacts, 'artifacts_pending': True})
                if not cancelled():
                    _publish_page_artifacts(fs, db, job_id, s3_prefix, page_number)
            if cancelled():
                return
            if _pages_all_terminal(db, job_id, page_count):
                _detect_characters(db, job_id, worker_client, settings)
            _refresh_completion(db, job_id)
    except _SERVICE_ERRORS as exc:
        logger.exception('background job %s: worker aborted', job_id)
        if not cancel_event.is_set():
            _set_job_if_active(db, job_id, {'status': 'blocked', 'worker_error': str(exc),
                                          'updated_at': _now()})


def _set_job_if_active(db, job_id, fields):
    ref = _job_ref(db, job_id)
    def commit(transaction):
        job = ref.get(transaction=transaction).to_dict() or {}
        if not job or job.get('status') in ('cancelled', 'finalized'):
            return False
        transaction.set(ref, fields, merge=True)
        return True
    return _atomic(db, commit)


def _refresh_completion(db, job_id):
    """Never mistake another worker's unfinished claims for complete extraction."""
    ref = _job_ref(db, job_id)
    def refresh(transaction):
        job = ref.get(transaction=transaction).to_dict() or {}
        if job.get('status') in ('cancelled', 'finalized'):
            return
        pages = [(_page_ref(db, job_id, n).get(transaction=transaction).to_dict() or {})
                 for n in range(1, job.get('page_count', 0) + 1)]
        finished = bool(pages) and all(p.get('status') in ('done', 'failed')
                                       and not p.get('artifacts_pending') for p in pages)
        complete = finished and job.get('character_status') in _CHARACTER_TERMINAL
        failed = sum(p.get('status') == 'failed' for p in pages)
        if complete:
            state = ('failed' if failed == len(pages) else 'partial'
                     if failed or job.get('character_status') == 'failed' else 'complete')
        else:
            state = 'waiting'
        transaction.set(ref, {'status': state, 'extraction_complete': complete,
                             'pages_done': sum(p.get('status') == 'done' for p in pages),
                             'pages_failed': failed, 'updated_at': _now()}, merge=True)
    return _atomic(db, refresh)


def _pages_all_terminal(db, job_id, page_count):
    """True when EVERY page ``1..page_count`` of the job has a terminal
    ('done'/'failed') result recorded (#201). A missing page doc (never
    claimed) or a 'processing' claim held by another worker counts as not
    terminal, so character detection is never computed over a partial book.
    """
    statuses = {}
    for page_snap in _job_ref(db, job_id).collection('pages').stream():
        data = page_snap.to_dict() or {}
        try:
            page_number = int(data.get('page_number', page_snap.id))
        except (TypeError, ValueError):
            continue
        statuses[page_number] = data.get('status')
    return all(
        statuses.get(page_number) in ('done', 'failed')
        for page_number in range(1, page_count + 1)
    )


def _detect_characters(db, job_id, client, settings):
    """Lease the current complete text revision before the existing whole-book call."""
    ref = _job_ref(db, job_id)
    worker_id = uuid.uuid4().hex

    def claim(transaction):
        job = ref.get(transaction=transaction).to_dict() or {}
        if job.get('status') in ('cancelled', 'finalized'):
            return None
        pages = []
        for number in range(1, job.get('page_count', 0) + 1):
            data = _page_ref(db, job_id, number).get(transaction=transaction).to_dict() or {}
            pages.append(dict(data, page_number=number))
        if not pages or any(p.get('status') not in ('done', 'failed') for p in pages):
            return None
        revision = _text_revision(pages)
        if job.get('character_status') in _CHARACTER_TERMINAL:
            if job.get('character_revision') == revision:
                return None
            if job.get('character_revision') is None and not job.get('processing_version'):
                # Preserve legacy completed paid work; a new explicit retry
                # transitions to pending before reaching this branch.
                transaction.set(ref, {'character_revision': revision,
                                      'character_input_revision': revision}, merge=True)
                return None
        claimed_at = job.get('character_claimed_at')
        if (job.get('character_status') == 'processing' and claimed_at is not None
                and claimed_at > _now() - timedelta(seconds=_CLAIM_LEASE_SECONDS)):
            return None
        # Reviewed suggestions remain available as an audit snapshot; retries
        # mark their revision stale instead of replacing accepted entities.
        transaction.set(ref, {
            'character_status': 'processing', 'character_claimed_by': worker_id,
            'character_claimed_at': _now(), 'character_input_revision': revision,
            'character_review_stale': bool(job.get('character_reviewed_revision')
                                          and job.get('character_reviewed_revision') != revision),
        }, merge=True)
        return pages, revision

    claimed = _atomic(db, claim)
    if claimed is None:
        return
    pages, revision = claimed
    story_pages = [(p['page_number'], p.get('text')) for p in pages
                   if p.get('status') == 'done' and p.get('is_story')
                   and isinstance(p.get('text'), str) and p.get('text').strip()]
    coverage = 'incomplete' if any(p.get('status') == 'failed' for p in pages) else 'complete'
    fields = {'character_revision': revision, 'character_pages_used': len(story_pages),
              'character_coverage': coverage, 'updated_at': _now()}
    if not story_pages:
        fields.update(character_status='skipped', character_outcome='no_text',
                      character_suggestions=[])
        _character_write(db, job_id, worker_id, revision, fields)
        return
    try:
        with _renewing_lease(lambda: _character_write(
            db, job_id, worker_id, revision, {'character_claimed_at': _now()},
        )):
            suggestions = detect_book_characters(
                story_pages, client, model=settings.get('character_detection_model'),
            )
            if not isinstance(suggestions, list):
                raise ValueError('Character detector returned no suggestion list')
            fields.update(character_status='done', character_suggestions=suggestions,
                          character_outcome='suggestions' if suggestions else 'empty',
                          character_error=None)
            _character_write(db, job_id, worker_id, revision, fields)
    except _SERVICE_ERRORS as exc:
        logger.warning('background job %s: character detection failed: %s', job_id, exc)
        fields.update(character_status='failed', character_outcome='failed', character_error=str(exc))
        _character_write(db, job_id, worker_id, revision, fields)


# ---------------------------------------------------------------------------
# Worker lifecycle (spawn / resume / cancel).
# ---------------------------------------------------------------------------

def _scheduler_loop():
    global _SCHEDULER_THREAD
    while True:
        with _REGISTRY_LOCK:
            if not _WORK_QUEUE:
                _SCHEDULER_THREAD = None
                return
            job_id = _WORK_QUEUE.popleft()
            entry = _ACTIVE_WORKERS.get(job_id)
        if entry is None:
            continue
        fs, db, client = entry['dependencies']
        try:
            data = _job_ref(db, job_id).get().to_dict() or {}
            if data and not entry['cancel'].is_set() and data.get('status') not in ('cancelled', 'finalized'):
                _run_worker(fs, db, client, data.get('ai_settings') or entry['settings'],
                            job_id, data['s3_prefix'], data['page_count'], None, entry['cancel'])
        except Exception as exc:  # final isolation boundary: logged and persisted, never silent
            logger.exception('pipeline scheduler: job %s failed', job_id)
            try:
                _set_job_if_active(db, job_id, {'status': 'blocked', 'worker_error': str(exc)})
            except _SERVICE_ERRORS as persistence_error:
                logger.warning('pipeline scheduler: job %s status could not be saved: %s',
                               job_id, persistence_error)
        finally:
            with _REGISTRY_LOCK:
                if _ACTIVE_WORKERS.get(job_id) is entry:
                    _ACTIVE_WORKERS.pop(job_id, None)


def _spawn_worker(fs, db, client, settings, job_id, s3_prefix, page_count, raw_bytes_list):
    """Queue identifiers through the one-book process-wide admission limit."""
    global _SCHEDULER_THREAD
    # Legacy callers pass bytes. Stage them before enqueueing so waiting jobs
    # cannot retain whole books in RAM. New batch callers pass durable refs.
    if raw_bytes_list is not None:
        if not _stage_sources(fs, db, job_id, s3_prefix, page_count, raw_bytes_list,
                              lambda: (_job_ref(db, job_id).get().to_dict() or {}).get('status') == 'cancelled'):
            return None
    with _REGISTRY_LOCK:
        entry = _ACTIVE_WORKERS.get(job_id)
        if entry is not None:
            return entry
        if _SCHEDULER_THREAD is None or not _SCHEDULER_THREAD.is_alive():
            _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop,
                                                name='book-pipeline-scheduler', daemon=True)
            start = True
        else:
            start = False
        entry = {'thread': _SCHEDULER_THREAD, 'cancel': threading.Event(),
                 'dependencies': (fs, db, client), 'settings': dict(settings)}
        _ACTIVE_WORKERS[job_id] = entry
        _WORK_QUEUE.append(job_id)
        if start:
            _SCHEDULER_THREAD.start()
        return entry


def create_or_get_job(fs, db, client, ai_settings, *, job_id, source_keys,
                      entered_by, book_id=None, batch_id=None, start=False):
    """Create an immutable explicit job, or recover that same identity safely."""
    if not job_id or '/' in job_id:
        raise ValueError('Invalid pipeline job identity')
    sources = list(source_keys)
    if not sources or any(not isinstance(key, str) or not key for key in sources):
        raise ValueError('A job requires ordered, durable source keys')
    settings = _validated_settings(ai_settings)
    source_hash = hashlib.sha256(json.dumps(sources).encode()).hexdigest()
    ref = _job_ref(db, job_id)
    def create(transaction):
        snap = ref.get(transaction=transaction)
        if snap.exists:
            data = snap.to_dict() or {}
            if (data.get('source_hash') != source_hash or data.get('entered_by') != entered_by
                    or data.get('book_id') != book_id or data.get('batch_id') != batch_id):
                raise ValueError('Pipeline job identity already binds different input')
            return data
        working_title = _placeholder_title(job_id)
        data = {'job_id': job_id, 'entered_by': entered_by, 'book_id': book_id,
                'batch_id': batch_id, 'page_count': len(sources), 'source_hash': source_hash,
                's3_prefix': _s3_prefix_for(working_title), 'working_title': working_title,
                'ai_settings': settings, 'processing_version': PROCESSING_VERSION,
                'raw_uploaded': False, 'status': 'queued', 'extraction_complete': False,
                'character_status': 'pending', 'character_suggestions': None,
                'finalization_status': 'pending', 'created_at': _now(), 'updated_at': _now()}
        transaction.set(ref, data)
        return data
    data = _atomic(db, create)
    # Immutable parent hash makes interrupted source-doc creation replayable.
    for number, key in enumerate(sources, 1):
        _page_ref(db, job_id, number).set({'page_number': number, 'source_key': key}, merge=True)
    link = {key: data.get(key) for key in ('job_id', 's3_prefix', 'working_title')}
    if start:
        resume_job(fs, db, client, job_id)
    return link


def _validated_settings(settings):
    """Freeze existing validated app settings, rejecting malformed overrides."""
    from utilities import (AI_SETTINGS_DEFAULTS, AI_SETTINGS_MODEL_KEYS,
                           AI_MODEL_ALLOWLIST, AI_EDGE_MIN, AI_EDGE_MAX,
                           AI_TOKENS_MIN, AI_TOKENS_MAX)
    result = dict(AI_SETTINGS_DEFAULTS)
    for key, value in dict(settings).items():
        if key not in result:
            continue
        if key in AI_SETTINGS_MODEL_KEYS:
            valid = value in AI_MODEL_ALLOWLIST
        elif key.endswith('_max_edge'):
            valid = isinstance(value, int) and not isinstance(value, bool) and AI_EDGE_MIN <= value <= AI_EDGE_MAX
        elif key.endswith('_max_tokens'):
            valid = isinstance(value, int) and not isinstance(value, bool) and AI_TOKENS_MIN <= value <= AI_TOKENS_MAX
        else:
            valid = isinstance(value, bool)
        if not valid:
            raise ValueError(f'Invalid pipeline setting: {key}')
        result[key] = value
    return result


def resume_job(fs, db, client, job_id):
    """Resume persisted sources/configuration without any original browser bytes."""
    data = _job_ref(db, job_id).get().to_dict() or {}
    if not data:
        raise ValueError('Pipeline job does not exist')
    if data.get('status') in ('cancelled', 'finalized') or data.get('extraction_complete'):
        return None
    if data.get('processing_version', PROCESSING_VERSION) != PROCESSING_VERSION:
        raise ValueError('Pipeline job requires its original processing version')
    settings = data.get('ai_settings')
    if not settings:
        raise ValueError('Legacy job requires settings from the single-book resume flow')
    return _spawn_worker(fs, db, client, settings, job_id, data['s3_prefix'], data['page_count'], None)


def get_job_progress(db, job_id):
    """Nonblocking snapshot; no worker starts and no paid work is triggered."""
    data = _job_ref(db, job_id).get().to_dict() or {}
    if not data:
        return None
    data.pop('character_suggestions', None)
    for key in ('pages_done', 'pages_failed', 'pages_staged'):
        data.setdefault(key, 0)
    return data


def cancel_job(db, job_id):
    """Durable cancellation invalidates all late page and character commits."""
    with _REGISTRY_LOCK:
        entry = _ACTIVE_WORKERS.get(job_id)
        if entry is not None:
            entry['cancel'].set()
    _job_ref(db, job_id).set({'status': 'cancelled', 'updated_at': _now()}, merge=True)


def retry_failed_pages(db, job_id):
    """Explicit retry invalidates character suggestions; ordinary reruns do not."""
    ref = _job_ref(db, job_id)
    def retry(transaction):
        job = ref.get(transaction=transaction).to_dict() or {}
        if job.get('status') in ('cancelled', 'finalized') or not job.get('extraction_complete'):
            return False
        pages = [(n, _page_ref(db, job_id, n),
                  _page_ref(db, job_id, n).get(transaction=transaction).to_dict() or {})
                 for n in range(1, job.get('page_count', 0) + 1)]
        failed = [(n, page_ref) for n, page_ref, data in pages if data.get('status') == 'failed']
        if not failed and job.get('character_status') != 'failed':
            return False
        for number, page_ref in failed:
            transaction.set(page_ref, {'status': 'pending', 'claimed_by': None,
                                       'claimed_at': None}, merge=True)
        transaction.set(ref, {'status': 'queued', 'extraction_complete': False,
                             'character_status': 'pending', 'character_revision': None,
                             'character_suggestions': None, 'character_review_stale': True,
                             'finalization_status': 'pending',
                             'materialized_character_revision': None,
                             'pages_failed': 0, 'updated_at': _now()}, merge=True)
        return True
    return _atomic(db, retry)


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
        ensure_worker_running(fs, db, client, ai_settings, existing, raw_bytes_list)
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
        except _SERVICE_ERRORS as exc:  # noqa: BLE001 - existence probe must never block job start
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
        'ai_settings': _validated_settings(ai_settings),
        'processing_version': PROCESSING_VERSION,
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


def ensure_worker_running(fs, db, client, ai_settings, link, raw_bytes_list=None):
    """Compatibility resume wrapper; persisted settings win over current UI settings."""
    data = _job_ref(db, link['job_id']).get().to_dict() or {}
    if not data or data.get('status') in ('cancelled', 'finalized') or data.get('extraction_complete'):
        return
    if not data.get('ai_settings'):
        _job_ref(db, link['job_id']).set({'ai_settings': _validated_settings(ai_settings)}, merge=True)
    _spawn_worker(fs, db, client, data.get('ai_settings') or ai_settings, link['job_id'],
                  data.get('s3_prefix', link.get('s3_prefix')), data.get('page_count', 0), raw_bytes_list)


def stamp_book_id(db, link, book_id):
    """Record the book id on the job doc once it is known (at consume), so a
    fresh session that lost its in-memory link could rediscover the job by book
    id. Best-effort."""
    try:
        _job_ref(db, link['job_id']).set({'book_id': book_id, 'updated_at': _now()}, merge=True)
    except _SERVICE_ERRORS as exc:  # noqa: BLE001 - non-critical linkage write
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
            target = f"{final_prefix}/{name}"
            fs.mv(key, target)  # server-side copy + delete
            if not fs.exists(target):
                raise OSError(f'Missing reconciled image: {name}')
        # Drop the now-empty working-prefix marker, if the store keeps one.
        if fs.exists(working_prefix):
            fs.rm(working_prefix, recursive=True)
    except _SERVICE_ERRORS as exc:
        logger.warning("background job %s: S3 reconcile %s -> %s failed: %s",
                       link['job_id'], working_prefix, final_prefix, exc)
        raise
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
    except _SERVICE_ERRORS as exc:  # noqa: BLE001 - non-critical status write
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
        except _SERVICE_ERRORS as exc:  # noqa: BLE001 - best-effort cancel
            logger.warning("background job %s: could not mark cancelled: %s", link['job_id'], exc)


def clear_page_processing_job(session_state):
    """Drop the finished job link from the session once it has been consumed."""
    session_state.pop(JOB_STATE_KEY, None)
