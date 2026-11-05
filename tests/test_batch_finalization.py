"""Durable batch finalization/review against transactional and storage fakes."""

import io
from types import SimpleNamespace

import pytest

import batch_finalization as bf


class Snapshot:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return None if self.value is None else dict(self.value)


class Reference:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.id = path.rsplit('/', 1)[-1]

    def get(self, transaction=None):
        return Snapshot(self.db.store.get(self.path))

    def set(self, value, merge=False):
        previous = self.db.store.get(self.path, {}) if merge else {}
        self.db.store[self.path] = {**previous, **value}

    def collection(self, name):
        return Collection(self.db, f'{self.path}/{name}')


class Collection:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, name):
        return Reference(self.db, f'{self.path}/{name}')


class Transaction:
    def __init__(self, db):
        self.db = db
        self.writes = []

    def create(self, ref, value):
        assert ref.path not in self.db.store
        self.writes.append((ref, value, False))

    def set(self, ref, value, merge=False):
        self.writes.append((ref, value, merge))

    def commit(self):
        for ref, value, merge in self.writes:
            ref.set(value, merge=merge)


class Database:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return Collection(self, name)

    def transaction(self):
        return Transaction(self)


class Filesystem:
    def __init__(self):
        self.store = {}
        self.fail_copy = None

    def exists(self, path):
        return path in self.store

    def open(self, path, mode):
        if path not in self.store:
            raise FileNotFoundError(path)
        return io.BytesIO(self.store[path])

    def copy(self, source, target):
        if self.fail_copy == source:
            raise OSError('copy unavailable')
        self.store[target] = self.store[source]


class Book:
    title = 'Test Book'
    document_id = 'test_book'
    photos_uploaded = False
    photos_url = ''
    page_count = -1

    def __init__(self, db):
        self.db = db

    def get_ref(self):
        return self.db.collection('books').document(self.document_id)


@pytest.fixture
def setup(monkeypatch):
    def transactional(function):
        def wrapped(transaction):
            result = function(transaction)
            transaction.commit()
            return result
        return wrapped

    monkeypatch.setattr(bf.firestore, 'transactional', transactional)
    db, fs = Database(), Filesystem()
    book = Book(db)
    book.get_ref().set({'pipeline_job_id': 'job'})
    db.store['pipeline_jobs/job'] = {
        'job_id': 'job', 'book_id': book.document_id,
        's3_prefix': 'sawimages/__pending__job', 'page_count': 2,
        'status': 'partial', 'extraction_complete': True,
        'character_status': 'done', 'character_outcome': 'suggestions',
        'character_suggestions': [{'name': 'Mouse', 'aliases': ['the Mouse']}],
        'character_coverage': 'incomplete',
        'character_revision': 'revision-1', 'character_input_revision': 'revision-1',
        'entered_by': Reference(db, 'users/alice'),
    }
    for number in (1, 2):
        db.store[f'pipeline_jobs/job/pages/{number}'] = {
            'page_number': number, 'status': 'done' if number == 1 else 'failed',
            'text': 'Mouse ran home.' if number == 1 else '', 'is_story': number == 1,
            'page_type': 'story', 'corrected': True, 'rotation_uncertain': number == 2,
        }
        for suffix in ('', '_cropped', '_display'):
            fs.store[f'sawimages/__pending__job/page_{number}{suffix}.jpg'] = (
                f'image-{number}-{suffix}'.encode()
            )
    return db, fs, book


def _pages(db):
    return {
        number: bf.Page(db.store[f'pages/test_book_{number}'])
        for number in (1, 2)
    }


def test_partial_materialization_preserves_correction_flags_and_book_scope(setup):
    db, fs, book = setup
    result = bf.finalize_batch_job(book, db, fs, 'job')
    assert result == {
        'status': 'partial', 'failed_pages': [2], 'created_pages': 2,
        'preserved_pages': 0, 'character_status': 'done',
    }
    failed_page = db.store['pages/test_book_2']
    assert failed_page['corrected'] is True
    assert failed_page['rotation_uncertain'] is True
    assert failed_page['text'] == ''
    assert failed_page['book'].id == book.document_id
    assert book.photos_url == 'sawimages/Test Book'
    assert book.photos_uploaded and book.page_count == 2
    assert db.store['pipeline_jobs/job']['s3_prefix'] == 'sawimages/__pending__job'


def test_replay_never_replaces_human_text_or_images(setup):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    db.store['pages/test_book_1']['text'] = 'Researcher corrected this.'
    fs.store['sawimages/Test Book/page_1_cropped.jpg'] = b'human crop'
    bf.finalize_batch_job(book, db, fs, 'job')
    assert db.store['pages/test_book_1']['text'] == 'Researcher corrected this.'
    assert fs.store['sawimages/Test Book/page_1_cropped.jpg'] == b'human crop'


def test_copy_failure_does_not_advance_book_and_retry_resumes(setup):
    db, fs, book = setup
    fs.fail_copy = 'sawimages/__pending__job/page_2.jpg'
    with pytest.raises(OSError):
        bf.finalize_batch_job(book, db, fs, 'job')
    assert book.photos_uploaded is False
    assert book.photos_url == ''
    assert 'pages/test_book_1' not in db.store
    assert db.store['pipeline_jobs/job']['finalization_status'] == 'copying'
    fs.fail_copy = None
    assert bf.finalize_batch_job(book, db, fs, 'job')['created_pages'] == 2


def test_collision_is_loud_and_never_overwrites_existing_image(setup):
    db, fs, book = setup
    fs.store['sawimages/Test Book/page_1.jpg'] = b'unrelated book'
    with pytest.raises(bf.BatchFinalizationError, match='mismatch'):
        bf.finalize_batch_job(book, db, fs, 'job')
    assert fs.store['sawimages/Test Book/page_1.jpg'] == b'unrelated book'
    assert book.photos_uploaded is False


def test_missing_required_corrected_image_blocks_materialization(setup):
    db, fs, book = setup
    del fs.store['sawimages/__pending__job/page_1_cropped.jpg']
    with pytest.raises(bf.BatchFinalizationError, match='Missing saved image'):
        bf.finalize_batch_job(book, db, fs, 'job')
    assert book.photos_uploaded is False


def test_all_failed_pages_never_claim_usable_empty_book(setup):
    db, fs, book = setup
    db.store['pipeline_jobs/job/pages/1']['status'] = 'failed'
    assert bf.finalize_batch_job(book, db, fs, 'job')['status'] == 'failed'
    assert 'pages/test_book_1' not in db.store
    assert book.photos_uploaded is False


def test_incomplete_job_does_not_block_or_materialize(setup):
    db, fs, book = setup
    db.store['pipeline_jobs/job']['extraction_complete'] = False
    assert bf.finalize_batch_job(book, db, fs, 'job') == {'status': 'waiting'}
    assert 'pages/test_book_1' not in db.store


def test_concurrent_retry_cannot_be_marked_finalized_by_old_consumer(setup):
    db, fs, _book = setup

    class RetriedBook(Book):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == 'photos_uploaded' and value:
                self.db.store['pipeline_jobs/job']['extraction_complete'] = False

    with pytest.raises(bf.BatchFinalizationError, match='changed during finalization'):
        bf.finalize_batch_job(RetriedBook(db), db, fs, 'job')
    assert db.store['pipeline_jobs/job']['finalization_status'] != 'complete'


def test_interrupted_finalization_protects_preexisting_page(setup):
    db, fs, book = setup
    existing = {'text': 'Human edit before recovery', 'page_number': 1, 'book': book.get_ref()}
    db.store['pages/test_book_1'] = existing.copy()
    result = bf.finalize_batch_job(book, db, fs, 'job')
    assert result['preserved_pages'] == 1
    assert db.store['pages/test_book_1'] == existing
    state = {'book_pages_dict': _pages(db)}
    bf.stage_batch_character_review(state, book, db)
    assert state['_batch_character_result']['status'] == 'stale'


@pytest.mark.parametrize('human_edited', [False, True])
def test_retry_updates_only_untouched_failed_placeholder(setup, human_edited):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    job = db.store['pipeline_jobs/job']
    job['finalization_status'] = 'pending'
    result = db.store['pipeline_jobs/job/pages/2']
    result.update(status='done', text='Recovered OCR.', is_story=True)
    if human_edited:
        db.store['pages/test_book_2'].update(text='Human text.', last_updated=42)
    fs.store['sawimages/__pending__job/page_2_cropped.jpg'] = b'retried correction'
    bf.finalize_batch_job(book, db, fs, 'job')
    assert db.store['pages/test_book_2']['text'] == ('Human text.' if human_edited else 'Recovered OCR.')
    assert fs.store['sawimages/Test Book/page_2_cropped.jpg'] == b'retried correction'


def test_retry_does_not_replace_human_corrected_image(setup):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    db.store['pipeline_jobs/job']['finalization_status'] = 'pending'
    fs.store['sawimages/Test Book/page_2_cropped.jpg'] = b'human crop'
    fs.store['sawimages/__pending__job/page_2_cropped.jpg'] = b'retry crop'
    with pytest.raises(bf.BatchFinalizationError, match='mismatch'):
        bf.finalize_batch_job(book, db, fs, 'job')
    assert fs.store['sawimages/Test Book/page_2_cropped.jpg'] == b'human crop'


def test_empty_success_is_durable_and_never_stages_paid_detection(setup):
    db, fs, book = setup
    db.store['pipeline_jobs/job'].update(character_suggestions=[], character_outcome='empty')
    bf.finalize_batch_job(book, db, fs, 'job')
    state = {'book_pages_dict': _pages(db), '_auto_run_detection': True, '_pending_character_autodetect': True}
    assert bf.stage_batch_character_review(state, book, db) is True
    assert state['_detected_characters'] == []
    assert state['now_entering'] == 'detect'
    assert '_auto_run_detection' not in state
    assert '_pending_character_autodetect' not in state


def test_unresolved_only_raw_response_survives_batch_handoff(setup):
    db, fs, book = setup
    raw = {'characters': [], 'unresolved_mentions': [{'surface': 'someone', 'page': 1}]}
    db.store['pipeline_jobs/job'].update(character_suggestions=[], character_outcome='empty',
                                         character_raw_response=raw)
    bf.finalize_batch_job(book, db, fs, 'job')
    state = {'book_pages_dict': _pages(db)}
    bf.stage_batch_character_review(state, book, db)
    assert state['_detected_characters'] == []
    assert state['_detected_characters'].raw_response == raw
    assert state['_batch_character_result']['raw_response'] == raw


def test_region_revision_matches_materialized_pages_and_detects_changes():
    evidence = {'regions': [{'kind': 'narrative', 'text': 'Story'},
                            {'kind': 'paratext', 'text': 'Blurb'}]}
    page = SimpleNamespace(text='Story\n\nBlurb', contains_story=True, extraction_evidence=evidence)
    fields = {1: {'status': 'done', 'text': page.text, 'is_story': True,
                  'page_type': 'narrative', 'extraction_evidence': evidence}}
    revision = bf._result_text_revision(fields)
    assert bf.page_text_revision({1: page}) == revision
    page.extraction_evidence = {'regions': [{'kind': 'narrative', 'text': 'Story'},
                                          {'kind': 'illustration_lettering', 'text': 'Blurb'}]}
    assert bf.page_text_revision({1: page}) != revision


def test_review_preserves_aliases_and_clears_previous_book_widget_values(setup):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    state = {'book_pages_dict': _pages(db), 'rev_name_0': 'Old book character'}
    bf.stage_batch_character_review(state, book, db)
    assert state['_detected_characters'] == [{'name': 'Mouse', 'aliases': ['the Mouse']}]
    assert 'rev_name_0' not in state


def test_page_edits_invalidate_saved_suggestions_without_rerunning(setup):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    pages = _pages(db)
    pages[1] = SimpleNamespace(text='A new character appeared.', contains_story=True)
    state = {'book_pages_dict': pages}
    bf.stage_batch_character_review(state, book, db)
    assert state['_batch_character_result']['status'] == 'stale'
    assert '_detected_characters' not in state
    assert '_auto_run_detection' not in state


def test_retry_revision_invalidates_old_suggestions(setup):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    db.store['pipeline_jobs/job']['character_revision'] = 'revision-2'
    assert bf.load_book_character_result(book, db)['status'] == 'stale'


def test_reviewed_revision_survives_new_session_and_stale_commit_is_rejected(setup):
    db, fs, book = setup
    bf.finalize_batch_job(book, db, fs, 'job')
    assert bf.mark_batch_character_reviewed(db, book.document_id, 'job', 'revision-1') is True
    state = {'book_pages_dict': _pages(db), 'now_entering': 'text'}
    bf.stage_batch_character_review(state, book, db)
    assert state['now_entering'] == 'text'
    assert '_detected_characters' not in state
    db.store['pipeline_jobs/job']['character_revision'] = 'revision-2'
    assert bf.mark_batch_character_reviewed(db, book.document_id, 'job', 'revision-1') is False


def test_book_identity_is_checked_before_reading_or_materializing_results(setup):
    db, fs, book = setup
    db.store['pipeline_jobs/job']['book_id'] = 'other_book'
    with pytest.raises(bf.BatchFinalizationError, match='belong'):
        bf.finalize_batch_job(book, db, fs, 'job')
    with pytest.raises(bf.BatchFinalizationError, match='another book'):
        bf.load_book_character_result(book, db)
