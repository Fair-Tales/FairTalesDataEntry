"""Durable coordination uses local storage and injected fake AI only."""
import copy
import io
import threading

import pytest
from google.api_core.exceptions import AlreadyExists

import batch_pipeline as coordinator
from data_structures.batch import BatchBook, BatchGroup


class Snapshot:
    def __init__(self, ref):
        self.id = ref.id
        self.data = copy.deepcopy(ref.db.data.get(ref.path))
        self.exists = self.data is not None

    def to_dict(self):
        return copy.deepcopy(self.data)


class Ref:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.id = path.rsplit('/', 1)[-1]

    def __deepcopy__(self, memo):
        return self

    def get(self, **kwargs):
        return Snapshot(self)

    def create(self, data):
        if self.path in self.db.data:
            raise AlreadyExists('exists')
        self.db.data[self.path] = copy.deepcopy(data)

    def set(self, data, merge=False):
        self.db.data[self.path] = {**(self.db.data.get(self.path, {}) if merge else {}), **copy.deepcopy(data)}

    def update(self, data):
        assert self.path in self.db.data
        self.set(data, merge=True)

    def collection(self, name):
        return Collection(self.db, f'{self.path}/{name}')


class Collection:
    def __init__(self, db, path, query=None):
        self.db, self.path, self.query = db, path, query

    def document(self, key):
        return Ref(self.db, f'{self.path}/{key}')

    def where(self, *, filter):
        return Collection(self.db, self.path, filter)

    def stream(self):
        for path, data in list(self.db.data.items()):
            if path.rsplit('/', 1)[0] != self.path:
                continue
            if self.query and data.get(self.query.field_path) != self.query.value:
                continue
            yield Ref(self.db, path).get()


class Transaction:
    def __init__(self, db):
        self.db = db

    def create(self, ref, fields):
        ref.create(fields)

    def update(self, ref, fields):
        ref.update(fields)

    def set(self, ref, fields, merge=False):
        ref.set(fields, merge)


class Db:
    def __init__(self):
        self.data = {}
        self.lock = threading.RLock()

    def collection(self, name):
        return Collection(self, name)

    def transaction(self):
        return Transaction(self)


class Fs:
    def __init__(self):
        self.data = {}
        self.fail = None

    def exists(self, path):
        return path in self.data

    def copy(self, source, target):
        self.data[target] = self.data[source]

    def open(self, path, mode='rb'):
        if path == self.fail:
            raise OSError('injected copy failure')
        if mode == 'rb':
            if path not in self.data:
                raise FileNotFoundError(path)
            return io.BytesIO(self.data[path])
        fs = self
        class Buffer(io.BytesIO):
            def __exit__(self, *exc):
                fs.data[path] = self.getvalue()
                self.close()
        return Buffer()


@pytest.fixture
def db(monkeypatch):
    def transactional(fn):
        def run(txn, *args, **kwargs):
            with txn.db.lock:
                original = copy.deepcopy(txn.db.data)
                try:
                    return fn(txn, *args, **kwargs)
                except BaseException:
                    txn.db.data = original
                    raise
        return run
    monkeypatch.setattr(coordinator.firestore, 'transactional', transactional)
    return Db()


def make_book(metadata, ordinal):
    book = BatchBook()
    book.title = metadata.get('title') or f'Book {ordinal}'
    return book


def seed_batch(db, count=3):
    batch = coordinator.create_batch(db, 'alice', {}, batch_id='batch')
    fs = Fs()
    sources = []
    for n in range(1, count + 1):
        key = f'uploads/{n}.jpg'
        fs.data[key] = f'photo {n}'.encode()
        sources.append({'position': n, 'name': f'{n}.jpg', 'key': key})
    return batch, fs, sources


def test_staging_resume_preserves_order_and_sources(db):
    batch, fs, sources = seed_batch(db)
    fs.fail = 'uploads/2.jpg'
    with pytest.raises(OSError):
        coordinator.stage_sources(batch, fs, sources)
    saved = coordinator.sources_for(db, 'batch')
    assert [source.staged for source in saved] == [True, False]
    fs.fail = None
    fs.data['uploads/1.jpg'] = b'changed browser upload'
    coordinator.stage_sources(batch, fs, sources)
    records = coordinator.sources_for(db, 'batch')
    assert fs.data[records[0].key] == b'photo 1'
    fresh = coordinator.load_batch(db, 'batch', 'alice')
    coordinator.freeze_sources(fresh, sources)
    assert fresh.state == 'detecting'
    assert [source.position for source in records] == [1, 2, 3]
    with pytest.raises(ValueError):
        coordinator.stage_sources(fresh, fs, sources)


def test_missing_failed_or_reordered_slots_block_freeze(db):
    batch, fs, sources = seed_batch(db)
    coordinator.stage_sources(batch, fs, sources[:2])
    with pytest.raises(ValueError):
        coordinator.freeze_sources(batch, sources)
    coordinator.stage_sources(batch, fs, sources)
    with pytest.raises(ValueError):
        coordinator.freeze_sources(batch, list(reversed(sources)))
    assert batch.state == 'uploading'


def test_discovery_and_owner_authorization(db):
    coordinator.create_batch(db, 'alice', {}, batch_id='mine')
    coordinator.create_batch(db, 'bob', {}, batch_id='theirs')
    assert [row['batch_id'] for row in coordinator.discover_batches(db, 'alice')] == ['mine']
    with pytest.raises(PermissionError):
        coordinator.load_batch(db, 'theirs', 'alice')


def group_for(db, batch, ordinal=1):
    group = BatchGroup(db, f'{batch.document_id}_{ordinal:04d}')
    group.batch_id = batch.document_id
    group.ordinal = ordinal
    group.metadata = {'title': 'Same title'}
    group.positions = [ordinal]
    group.register()
    return group


def test_confirmation_atomic_binding_prevents_duplicates(db):
    batch = coordinator.create_batch(db, 'alice', {}, batch_id='batch')
    group = group_for(db, batch)
    first = coordinator.bind_book(batch, group, make_book, 'users/alice')
    recovered = coordinator.bind_book(batch, group, make_book, 'users/alice')
    assert first.document_id == recovered.document_id
    assert len([key for key in db.data if key.startswith('books/')]) == 1
    assert recovered.pipeline_job_id == group.document_id
    assert recovered.batch_group_id == group.document_id


def test_competing_groups_same_title_create_distinct_books(db):
    batch = coordinator.create_batch(db, 'alice', {}, batch_id='batch')
    one, two = group_for(db, batch, 1), group_for(db, batch, 2)
    books = [coordinator.bind_book(batch, group, make_book, 'users/alice') for group in (one, two)]
    assert {book.title for book in books} == {'Same title', 'Same title (2)'}


def test_one_group_failure_does_not_abort_later_books(db, monkeypatch):
    import background_pipeline as pipeline
    batch, fs, sources = seed_batch(db, 2)
    coordinator.stage_sources(batch, fs, sources)
    for n in (1, 2):
        group_for(db, batch, n)
    batch.state = 'review'
    queued = []
    monkeypatch.setattr(pipeline, 'create_or_get_job', lambda *args, **kwargs: queued.append(kwargs))
    monkeypatch.setattr(pipeline, 'resume_job', lambda *args: None)
    def factory(metadata, ordinal):
        if ordinal == 1:
            raise ValueError('invalid first title')
        return make_book(metadata, ordinal)
    coordinator.confirm_batch(batch, fs, object(), factory, 'users/alice')
    groups = coordinator.groups_for(db, batch.document_id)
    assert [group.state for group in groups] == ['failed', 'queued']
    assert groups[0].error
    assert len(queued) == 1
    assert queued[0]['job_id'] == groups[1].document_id


def test_metadata_recovery_does_not_repeat_completed_or_unknown_calls(db, monkeypatch):
    batch, fs, sources = seed_batch(db, 2)
    coordinator.stage_sources(batch, fs, sources)
    coordinator.freeze_sources(batch, sources)
    monkeypatch.setattr(coordinator, 'downscale_for_vision', lambda image, **kwargs: image)
    calls = []
    def split(entries, read, client=None):
        assert read(entries[0]) == b'photo 1'
        return {'method': 'black_frame', 'groups': [[entries[0]], [entries[1]]]}
    def metadata(pages, client):
        calls.append(pages)
        if pages[0][1] == b'photo 1':
            raise OSError('temporary metadata failure')
        return {'title': 'Second book'}
    coordinator.detect_groups(batch, fs, object(), splitter=split, metadata_reader=metadata)
    fresh = coordinator.load_batch(db, 'batch', 'alice')
    groups = coordinator.detect_groups(fresh, fs, object(), splitter=split, metadata_reader=metadata)
    assert len(calls) == 2
    assert [group.metadata_state for group in groups] == ['failed', 'done']
    assert groups[1].metadata['title'] == 'Second book'


def test_unregistered_batch_book_descriptors_do_not_write(db):
    book = BatchBook()
    book.pipeline_job_id = 'job'
    assert book.to_dict()['pipeline_job_id'] == 'job'
    assert not db.data


@pytest.mark.parametrize('metadata', [[], {'title': 7}, {'authors': 'Mouse'}, {'illustrators': [None]}, {'published_year': True}])
def test_malformed_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        coordinator.validate_metadata(metadata)


def test_full_two_book_handoff_runs_character_and_alias_detection(db, monkeypatch):
    import streamlit as st
    import background_pipeline as pipeline
    import pages.uploader as uploader
    from PIL import Image
    from batch_finalization import load_book_character_result

    class State(dict):
        def __getattr__(self, name):
            return self[name]

    class Wrapper:
        def connect_book(self):
            return db

        def update_field(self, collection, document, field, value):
            db.collection(collection).document(document).update({field: value})

    monkeypatch.setattr(st, 'session_state', State(firestore=Wrapper(), username='alice'))
    batch, fs, sources = seed_batch(db, 3)
    for source, color in zip(sources, ('red', 'black', 'blue')):
        output = io.BytesIO()
        Image.new('RGB', (12, 12), color).save(output, format='PNG')
        fs.data[source['key']] = output.getvalue()
    coordinator.stage_sources(batch, fs, sources)
    coordinator.freeze_sources(batch, sources)
    coordinator.detect_groups(batch, fs, object(), metadata_reader=lambda pages, client: {
        'title': 'Mouse' if pages[0][0] == '1.jpg' else 'Fox',
    })
    monkeypatch.setattr(pipeline, 'exif_transpose_bytes', lambda raw: raw)
    monkeypatch.setattr(pipeline, 'make_display_copy', lambda raw: raw)
    monkeypatch.setattr(pipeline, 'correct_page_image', lambda raw, *args: (raw, raw, 'crop', True))
    monkeypatch.setattr(uploader, 'attempt_page_extraction', lambda raw, *args, **kwargs: (
        'ok', ('Mouse ran.' if Image.open(io.BytesIO(raw)).getpixel((0, 0))[0] else 'Fox ran.', True, 'story')))
    character_inputs = []
    def detect(pages, client, **kwargs):
        character_inputs.append(pages)
        name = pages[0][1].split()[0]
        return [{'name': name, 'aliases': [f'the {name.lower()}']}]
    monkeypatch.setattr(pipeline, 'detect_book_characters', detect)
    def run(fs, db, client, settings, job_id, prefix, count, raw):
        pipeline._run_worker(fs, db, client, settings, job_id, prefix, count, raw, threading.Event())
    monkeypatch.setattr(pipeline, '_spawn_worker', run)
    coordinator.confirm_batch(batch, fs, object(), make_book, 'users/alice')
    results = coordinator.finalize_ready(batch, fs)
    assert [result['status'] for result in results] == ['complete', 'complete']
    assert character_inputs == [[(1, 'Mouse ran.')], [(1, 'Fox ran.')]]
    for book_id, alias in [('mouse', 'the mouse'), ('fox', 'the fox')]:
        book = BatchBook(db.data[f'books/{book_id}'])
        result = load_book_character_result(book, db)
        assert result['suggestions'][0]['aliases'] == [alias]
        assert db.data[f'pages/{book_id}_1']['corrected'] is True
        assert db.data[f'pages/{book_id}_1']['rotation_uncertain'] is True
        assert book.photos_uploaded and book.page_count == 1
    # Fresh coordinator/session resume never repeats completed paid stages.
    fresh = coordinator.load_batch(db, batch.document_id, 'alice')
    coordinator.confirm_batch(fresh, fs, object(), make_book, 'users/alice')
    coordinator.finalize_ready(fresh, fs)
    assert len(character_inputs) == 2
    assert len([key for key in db.data if key.startswith('books/')]) == 2
