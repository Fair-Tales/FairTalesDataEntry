"""Materialize durable batch results on the script thread without further AI.

Book and Page entities remain explicit, write-through objects. Transactional
materialization markers live with the operational job, so a replay cannot
replace a page that a researcher has since edited.
"""

import copy
import hashlib
import json
from datetime import datetime, timezone

from google.cloud import firestore

from background_pipeline import PIPELINE_JOBS_COLLECTION, _fields_to_result
from data_structures import Page
from page_results import apply_page_correction
from s3_constants import S3_BUCKET


class BatchFinalizationError(ValueError):
    """Saved results or images are not safe to materialize yet."""


def _job_ref(db, job_id):
    return db.collection(PIPELINE_JOBS_COLLECTION).document(job_id)


def page_text_revision(pages):
    """Stable revision of Page entities, including blank/non-story pages."""
    rows = [
        [number, page.text or '', bool(page.contains_story)]
        for number, page in sorted(pages.items())
    ]
    return _text_rows_revision(rows)


def _text_rows_revision(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def _result_text_revision(results):
    rows = []
    for number, fields in sorted(results.items()):
        result = _fields_to_result(fields)
        if result is None:
            raise BatchFinalizationError('Page extraction is not terminal')
        outcome, payload, *_correction = result
        rows.append([number, payload[0] or '', bool(payload[1])] if outcome == 'ok' else [number, '', False])
    return _text_rows_revision(rows)


def _file_digest(fs, path):
    digest = hashlib.sha256()
    with fs.open(path, 'rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def _reconcile_images(fs, job_ref, job, final_prefix, page_results):
    """Copy and verify each expected object; retain sources for recovery.

    A copy failure deliberately propagates. Neither the book's photos_url nor
    the job's source prefix advances until every required object is verified.
    Existing objects must agree with the source; a collision is never replaced.
    """
    source_prefix = job.get('s3_prefix')
    if not source_prefix:
        raise BatchFinalizationError('Job has no source image prefix')
    for page_number, data in sorted(page_results.items()):
        saved_digests = data.get('materialized_images') or {}
        verified_digests = {}
        names = [f'page_{page_number}.jpg']
        if data.get('corrected'):
            names.append(f'page_{page_number}_cropped.jpg')
        display_name = f'page_{page_number}_display.jpg'
        if fs.exists(f'{source_prefix}/{display_name}'):
            names.append(display_name)
        for name in names:
            source = f'{source_prefix}/{name}'
            target = f'{final_prefix}/{name}'
            if not fs.exists(source):
                raise BatchFinalizationError(f'Missing saved image: {source}')
            if source != target:
                source_digest = _file_digest(fs, source).hex()
                if not fs.exists(target):
                    fs.copy(source, target)
                target_digest = _file_digest(fs, target).hex()
                if target_digest != source_digest and target_digest == saved_digests.get(name):
                    # An explicit extraction retry can replace its own previous
                    # image only while that exact image remains unchanged.
                    fs.copy(source, target)
                    target_digest = _file_digest(fs, target).hex()
                if source_digest != target_digest:
                    raise BatchFinalizationError(f'Image copy mismatch: {target}')
                verified_digests[name] = target_digest
            else:
                verified_digests[name] = _file_digest(fs, target).hex()
        job_ref.collection('pages').document(str(page_number)).set(
            {'materialized_images': verified_digests}, merge=True,
        )


def _materialize_page(book, db, job_id, page_number, result_fields, entered_by):
    """Atomically create a Page and its marker; preserve existing human edits.

    A retried failed extraction may replace its own untouched placeholder.
    Any edit changes last_updated via Field, and blocks that replacement.
    """
    result = _fields_to_result(result_fields)
    if result is None:
        raise BatchFinalizationError('Page extraction is not terminal')
    outcome, payload, _method, corrected, uncertain = result
    page = Page(page_number=page_number, book=book.get_ref())
    apply_page_correction(page, corrected=corrected, rotation_uncertain=uncertain)
    if outcome == 'ok':
        page.text, page.contains_story, _page_type = payload
    page.entered_by = entered_by
    page.datetime_created = datetime.now(timezone.utc)
    page.is_registered = True
    page_data = page.to_dict()
    target_ref = db.collection('pages').document(page.document_id)
    job_ref = _job_ref(db, job_id)
    result_ref = job_ref.collection('pages').document(str(page_number))

    @firestore.transactional
    def materialize(transaction):
        current_job = job_ref.get(transaction=transaction).to_dict() or {}
        current_result = result_ref.get(transaction=transaction).to_dict() or {}
        existing = target_ref.get(transaction=transaction).to_dict()
        if not current_job.get('extraction_complete'):
            raise BatchFinalizationError('Extraction changed during finalization')
        if _fields_to_result(current_result) != result:
            raise BatchFinalizationError('Page result changed during finalization')
        previous = current_result.get('materialized_page')
        if existing is None:
            transaction.create(target_ref, page_data)
            action = 'created'
        elif (
            current_result.get('materialized_outcome') == 'error'
            and outcome == 'ok'
            and isinstance(previous, dict)
            and all(existing.get(key) == value for key, value in previous.items())
        ):
            # Safe retry: exactly the previous pipeline placeholder, including
            # its unchanged last_updated, is still stored.
            transaction.set(target_ref, page_data)
            action = 'created'
        else:
            return 'preserved', existing
        transaction.set(result_ref, {
            'materialized_page': page_data,
            'materialized_outcome': outcome,
            'materialized_at': datetime.now(timezone.utc),
        }, merge=True)
        return action, page_data

    action, saved_data = materialize(db.transaction())
    return action, Page(saved_data)


def finalize_batch_job(book, db, fs, job_id):
    """Finalize one explicit Book from durable results, without blocking or AI.

    Returns a compact result. Incomplete jobs return ``waiting``. All-failed
    jobs return ``failed`` and never claim to have produced a usable book.
    Storage errors propagate to the coordinator's per-book isolation boundary.
    """
    ref = _job_ref(db, job_id)
    job = ref.get().to_dict() or {}
    if job.get('book_id') != book.document_id:
        raise BatchFinalizationError('Job does not belong to the supplied book')
    if not job.get('extraction_complete'):
        return {'status': 'waiting'}
    if job.get('finalization_status') == 'complete':
        return dict(job.get('finalization_result') or {'status': 'complete'})
    count = job.get('page_count', 0)
    if not isinstance(count, int) or count < 1:
        raise BatchFinalizationError('Job has no expected pages')
    results = {}
    for number in range(1, count + 1):
        data = ref.collection('pages').document(str(number)).get().to_dict() or {}
        if _fields_to_result(data) is None:
            return {'status': 'waiting'}
        results[number] = data
    failed = [number for number, data in results.items() if data.get('status') == 'failed']
    result = {
        'status': 'partial' if failed or job.get('character_status') == 'failed' else 'complete',
        'failed_pages': failed,
        'created_pages': 0,
        'preserved_pages': 0,
        'character_status': job.get('character_status'),
    }
    if len(failed) == count:
        result['status'] = 'failed'
        return result
    final_prefix = f'{S3_BUCKET}/{book.title}'
    prior_target = job.get('finalization_target')
    if prior_target and prior_target != final_prefix:
        raise BatchFinalizationError('Book title changed during finalization')
    ref.set({'finalization_status': 'copying', 'finalization_target': final_prefix}, merge=True)
    _reconcile_images(fs, ref, job, final_prefix, results)
    for number, data in sorted(results.items()):
        action, _page = _materialize_page(book, db, job_id, number, data, job.get('entered_by'))
        result[f'{action}_pages'] += 1
    # Fields write through only after verified images and durable Page records.
    book.photos_url = final_prefix
    book.page_count = count
    book.photos_uploaded = True
    completion = {
        'finalization_status': 'complete',
        'finalization_result': result,
        'final_prefix': final_prefix,
        # Keep s3_prefix as the durable worker location for explicit retries.
        # Compare review against the OCR input, not against preserved human
        # edits encountered during finalization; those already make it stale.
        'materialized_text_revision': _result_text_revision(results),
        'materialized_character_revision': job.get('character_revision'),
        'finalized_at': datetime.now(timezone.utc),
    }

    @firestore.transactional
    def complete(transaction):
        current = ref.get(transaction=transaction).to_dict() or {}
        if (
            not current.get('extraction_complete')
            or current.get('status') == 'cancelled'
            or current.get('character_revision') != job.get('character_revision')
        ):
            raise BatchFinalizationError('Extraction changed during finalization')
        transaction.set(ref, completion, merge=True)

    complete(db.transaction())
    return result


def load_book_character_result(book, db):
    """Find the durable handoff even when a normal Book load drops new fields."""
    book_data = book.get_ref().get().to_dict() or {}
    job_id = book_data.get('pipeline_job_id')
    if not job_id:
        return None
    job = _job_ref(db, job_id).get().to_dict() or {}
    if job.get('book_id') != book.document_id:
        raise BatchFinalizationError('Character result belongs to another book')
    revision = job.get('character_revision')
    result = {
        'job_id': job_id,
        'book_id': book.document_id,
        'revision': revision,
        'status': job.get('character_status', 'pending'),
        'outcome': job.get('character_outcome'),
        'coverage': job.get('character_coverage'),
        'suggestions': copy.deepcopy(job.get('character_suggestions') or []),
        'text_revision': job.get('materialized_text_revision'),
        'reviewed': bool(revision and job.get('character_reviewed_revision') == revision),
    }
    if (
        job.get('finalization_status') != 'complete'
        or revision != job.get('materialized_character_revision')
        or revision != job.get('character_input_revision')
    ):
        result['status'] = 'stale'
    return result


def stage_batch_character_review(session_state, book, db):
    """Stage saved suggestions (including empty success), never a paid call."""
    result = load_book_character_result(book, db)
    if result is None:
        session_state.pop('_batch_character_result', None)
        session_state.pop('_batch_character_review', None)
        return False
    pages = session_state.get('book_pages_dict', {})
    if result.get('text_revision') and page_text_revision(pages) != result['text_revision']:
        result['status'] = 'stale'
    previous = session_state.get('_batch_character_result') or {}
    if (previous.get('book_id'), previous.get('revision')) != (result['book_id'], result['revision']):
        for key in tuple(session_state):
            if key.startswith('rev_'):
                session_state.pop(key, None)
    session_state['_batch_character_result'] = result
    session_state.pop('_auto_run_detection', None)
    session_state.pop('_pending_character_autodetect', None)
    session_state.pop('_precomputed_character_suggestions', None)
    session_state.pop('_detected_characters', None)
    session_state.pop('_batch_character_review', None)
    if result['reviewed'] and result['status'] != 'stale':
        return True
    session_state['now_entering'] = 'detect'
    if result['status'] in ('done', 'skipped'):
        session_state['_detected_characters'] = result['suggestions']
        session_state['_detected_characters_source'] = 'auto'
        session_state['_batch_character_review'] = {
            'book_id': book.document_id, 'job_id': result['job_id'], 'revision': result['revision'],
        }
    return True


def mark_batch_character_reviewed(db, book_id, job_id, revision):
    """Record review only for the same book and still-current input revision."""
    ref = _job_ref(db, job_id)

    @firestore.transactional
    def mark(transaction):
        job = ref.get(transaction=transaction).to_dict() or {}
        if (
            job.get('book_id') != book_id
            or job.get('character_revision') != revision
            or job.get('character_input_revision') != revision
        ):
            return False
        transaction.set(ref, {'character_reviewed_revision': revision}, merge=True)
        return True

    return mark(db.transaction())
