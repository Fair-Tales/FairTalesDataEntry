"""Script-thread batch coordinator. Explicit services permit entirely offline tests.

One source/group per document bounds Firestore record sizes. Every accepted photo
is preserved before the upload component may advance; confirmation atomically
creates the Book and binds the group. Ordinary progress reads never start AI work.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timezone

import anthropic
from botocore.exceptions import BotoCoreError, ClientError
from google.api_core.exceptions import GoogleAPIError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from data_structures.batch import PhotoBatch, BatchSource, BatchGroup, BatchBook
from image_processing import downscale_for_vision
from s3_constants import S3_BUCKET, NON_BOOK_S3_PREFIXES

logger = logging.getLogger(__name__)
SERVICE_ERRORS = (OSError, ValueError, RuntimeError, GoogleAPIError,
                  BotoCoreError, ClientError, anthropic.AnthropicError)
MAX_BATCH_PHOTOS = 1200
MAX_METADATA_PHOTOS = 60


def create_batch(db, owner, settings, batch_id=None):
    batch = PhotoBatch(db, batch_id or uuid.uuid4().hex)
    batch.owner = owner
    from background_pipeline import _validated_settings
    batch.settings = _validated_settings(settings)
    batch = batch.create_or_load()
    if batch.owner != owner:
        raise PermissionError('Batch owner does not match')
    return batch


def load_batch(db, batch_id, owner):
    batch = PhotoBatch.load(db, batch_id)
    if batch.owner != owner:
        raise PermissionError('Batch owner does not match')
    return batch


def discover_batches(db, owner):
    query = db.collection(PhotoBatch.collection).where(filter=FieldFilter('owner', '==', owner))
    return sorted(({'batch_id': snap.id, **snap.to_dict()} for snap in query.stream()),
                  key=lambda row: str(row.get('datetime_created') or ''), reverse=True)


def _records(db, entity, batch_id, order):
    query = db.collection(entity.collection).where(filter=FieldFilter('batch_id', '==', batch_id))
    records = [entity(db, snap.id, snap.to_dict()) for snap in query.stream()]
    return sorted(records, key=lambda item: getattr(item, order))


def sources_for(db, batch_id):
    return _records(db, BatchSource, batch_id, 'position')


def groups_for(db, batch_id):
    return _records(db, BatchGroup, batch_id, 'ordinal')


def _read(fs, key):
    with fs.open(key, 'rb') as handle:
        return handle.read()


def stage_sources(batch, fs, sources):
    """Checkpoint each immutable source; replay verifies identity, never replaces it."""
    if batch.state != 'uploading':
        raise ValueError('The batch source order is frozen')
    for entry in sources:
        position = entry.get('position')
        if type(position) is not int or not 1 <= position <= MAX_BATCH_PHOTOS:
            raise ValueError('Invalid or excessive batch position')
        source = BatchSource(batch._db, f'{batch.document_id}_{position:06d}')
        source.batch_id = batch.document_id
        source.position = position
        source.name = entry.get('name') or f'page_{position}.jpg'
        source.original_key = entry.get('key') or ''
        source.key = f'{S3_BUCKET}/batch_sources/{batch.document_id}/{position}.jpg'
        source = source.create_or_load()
        if source.original_key != entry.get('key'):
            raise ValueError('A frozen upload position cannot be replaced')
        if source.staged:
            continue
        raw = _read(fs, source.original_key)
        if not raw:
            raise ValueError(f'Empty upload at position {position}')
        digest = hashlib.sha256(raw).hexdigest()
        with fs.open(source.key, 'wb') as handle:
            handle.write(raw)
        if hashlib.sha256(_read(fs, source.key)).hexdigest() != digest:
            raise OSError(f'Upload verification failed at position {position}')
        source.sha256 = digest
        source.staged = True
    staged = sources_for(batch._db, batch.document_id)
    batch.source_count = len(staged)


def freeze_sources(batch, expected_sources):
    records = sources_for(batch._db, batch.document_id)
    expected = list(expected_sources)
    if not expected or len(records) != len(expected):
        raise ValueError('The upload is incomplete')
    for position, (record, source) in enumerate(zip(records, expected), 1):
        if (record.position != position or not record.staged
                or source.get('position') != position
                or source.get('key') != record.original_key):
            raise ValueError('The upload order or completion state has changed')
    batch.state = 'detecting'


def detect_groups(batch, fs, client, *, splitter=None, metadata_reader=None):
    if client is None:
        return _detect_groups(batch, fs, client, splitter=splitter, metadata_reader=metadata_reader)
    from pipeline_worker_runtime import worker_runtime
    with worker_runtime(client, batch.settings, batch._db, None, batch.document_id) as bounded:
        return _detect_groups(batch, fs, bounded, splitter=splitter, metadata_reader=metadata_reader)


def _detect_groups(batch, fs, client, *, splitter=None, metadata_reader=None):
    """Persist boundaries before metadata calls, then checkpoint each outcome."""
    from batch_splitting import split_source_manifest
    from utilities import extract_photo_first_metadata
    splitter = splitter or split_source_manifest
    metadata_reader = metadata_reader or extract_photo_first_metadata
    records = sources_for(batch._db, batch.document_id)
    by_position = {source.position: source for source in records}
    if not records or any(not source.staged for source in records):
        raise ValueError('No complete durable source manifest')
    if not batch.group_count:
        manifest = [{'name': item.name, 'key': item.key, 'position': item.position} for item in records]
        split = splitter(manifest, lambda source: _read(fs, source['key']), client=client)
        groups = split.get('groups') or []
        if not groups:
            raise ValueError('No book groups detected')
        for ordinal, entries in enumerate(groups, 1):
            group = BatchGroup(batch._db, f'{batch.document_id}_{ordinal:04d}')
            group.batch_id = batch.document_id
            group.ordinal = ordinal
            group.positions = [entry['position'] for entry in entries]
            group = group.create_or_load()
            if group.positions != [entry['position'] for entry in entries]:
                raise ValueError('Saved split differs; start a new batch')
        batch.split_version = split.get('version', batch.split_version)
        batch.split_method = split.get('method', 'single')
        batch.group_count = len(groups)
    for group in groups_for(batch._db, batch.document_id):
        if group.metadata_state != 'pending':
            continue
        # A process death after this checkpoint leaves an explicit unknown result;
        # reopening must not silently repeat paid metadata work.
        group.metadata_state = 'processing'
        try:
            if client is None:
                raise ValueError('AI client unavailable')
            if any(pos not in by_position for pos in group.positions):
                raise ValueError('A metadata source record is missing')
            if len(group.positions) > MAX_METADATA_PHOTOS:
                raise ValueError('Metadata photo limit exceeded; enter details during review')
            pages = [(by_position[pos].name, downscale_for_vision(_read(fs, by_position[pos].key), max_edge=batch.settings.get('extraction_max_edge', 2000)))
                     for pos in group.positions]
            response = metadata_reader(pages, client)
            try:
                result = validate_metadata(response)
            except ValueError:
                group.metadata_failed_response = response
                raise
            group.metadata = result
            group.metadata_state = 'done'
        except SERVICE_ERRORS as exc:
            logger.exception('Batch metadata failed for %s', group.document_id)
            group.metadata_error = f'{type(exc).__name__}: {exc}'
            group.metadata_state = 'failed'
    batch.state = 'review'
    batch.error = ''
    return groups_for(batch._db, batch.document_id)


def validate_metadata(value):
    """Reject malformed provider values before they can poison review widgets."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Metadata response must be an object')
    if value.get('metadata_extraction_status') == 'invalid_response':
        raise ValueError('Invalid joint metadata response')
    for field in ('title', 'publisher'):
        if value.get(field) is not None and not isinstance(value[field], str):
            raise ValueError(f'Invalid metadata {field}')
    for field in ('authors', 'illustrators'):
        names = value.get(field)
        if names is not None and (not isinstance(names, list)
                                 or any(not isinstance(name, str) for name in names)):
            raise ValueError(f'Invalid metadata {field}')
    year = value.get('published_year')
    if year is not None and (type(year) is not int or not 1900 <= year <= datetime.now(timezone.utc).year):
        raise ValueError('Invalid metadata publication year')
    return value


def _book_payload(book, owner_ref):
    return {**book.to_dict(), 'is_registered': True, 'entered_by': owner_ref,
            'datetime_created': datetime.now(timezone.utc)}


def bind_book(batch, group, make_book, owner_ref):
    """Atomic create + binding, including competing batches with equal titles."""
    db = batch._db
    job_id = group.document_id
    candidate = make_book(group.metadata, group.ordinal)
    base_title = candidate.title
    for suffix in range(1, 1001):
        candidate.title = base_title if suffix == 1 else f'{base_title} ({suffix})'
        if ('/' in candidate.title or not candidate.title.strip()
                or candidate.title in ('.', '..', *NON_BOOK_S3_PREFIXES)
                or candidate.title.startswith('__pending__')):
            raise ValueError('Book title must be nonempty and must not contain a slash')
        candidate.pipeline_job_id = job_id
        candidate.batch_group_id = group.document_id
        book_ref = db.collection('books').document(candidate.document_id)
        group_ref = group.get_ref()

        @firestore.transactional
        def create(transaction):
            current = group_ref.get(transaction=transaction).to_dict() or {}
            existing_id = current.get('book_id')
            if existing_id:
                return existing_id
            if book_ref.get(transaction=transaction).exists:
                return None
            transaction.create(book_ref, _book_payload(candidate, owner_ref))
            transaction.update(group_ref, {'book_id': candidate.document_id,
                                          'job_id': job_id, 'state': 'queued', 'error': ''})
            return candidate.document_id

        book_id = create(db.transaction())
        if book_id:
            data = db.collection('books').document(book_id).get().to_dict()
            if not data:
                raise ValueError('Bound book is missing')
            return BatchBook(data)
    raise ValueError('Too many books with the same title')


def confirm_batch(batch, fs, client, make_book, owner_ref):
    """Replay-safe handoff. A group failure never prevents later groups queuing."""
    import background_pipeline as pipeline
    if batch.state not in ('review', 'processing'):
        raise ValueError('Batch has not been reviewed')
    batch.state = 'processing'
    records = {source.position: source for source in sources_for(batch._db, batch.document_id)}
    for group in groups_for(batch._db, batch.document_id):
        try:
            if any(pos not in records for pos in group.positions):
                raise ValueError('A group source record is missing')
            keys = [records[pos].key for pos in group.positions]
            if not keys:
                raise ValueError('Refusing to create an empty book')
            book = bind_book(batch, group, make_book, owner_ref)
            pipeline.create_or_get_job(
                fs, batch._db, client, batch.settings, job_id=group.document_id,
                source_keys=keys, entered_by=owner_ref, book_id=book.document_id,
                batch_id=batch.document_id, start=False,
            )
            group = BatchGroup.load(batch._db, group.document_id)
            if group.state != 'finalized':
                group.state = 'queued'
            group.error = ''
            pipeline.resume_job(fs, batch._db, client, group.job_id)
        except SERVICE_ERRORS as exc:
            logger.exception('Batch handoff failed for %s', group.document_id)
            group.error = f'{type(exc).__name__}: {exc}'
            group.state = 'failed'


def progress_snapshot(batch):
    import background_pipeline as pipeline
    rows = []
    for group in groups_for(batch._db, batch.document_id):
        row = {'group_id': group.document_id, 'book_id': group.book_id,
               'ordinal': group.ordinal, 'metadata': group.metadata,
               'metadata_error': group.metadata_error, 'error': group.error,
               'state': group.state, 'pages': len(group.positions), 'job_id': group.job_id}
        if group.job_id:
            row['progress'] = pipeline.get_job_progress(batch._db, group.job_id)
        rows.append(row)
    return rows


def finalize_ready(batch, fs):
    from batch_finalization import finalize_batch_job
    results = []
    for group in groups_for(batch._db, batch.document_id):
        if not group.job_id or group.state == 'finalized':
            continue
        try:
            snap = batch._db.collection('books').document(group.book_id).get()
            if not snap.exists:
                raise ValueError('Bound book is missing')
            result = finalize_batch_job(BatchBook(snap.to_dict()), batch._db, fs, group.job_id)
            if result.get('status') in ('complete', 'partial'):
                group.state = 'finalized'
                group.error = ''
            results.append(result)
        except SERVICE_ERRORS as exc:
            logger.exception('Batch finalization failed for %s', group.document_id)
            group.error = f'{type(exc).__name__}: {exc}'
    return results
