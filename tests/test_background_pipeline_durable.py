"""Regression coverage for resumable, owner-checked batch jobs; services are fakes."""
from datetime import timedelta
from functools import wraps
import threading
from types import SimpleNamespace

import pytest

import background_pipeline as bp
import pages.uploader as uploader
from pipeline_worker_runtime import worker_runtime
from test_background_pipeline import FakeClient, FakeFs, SETTINGS, _make_job
from test_background_pipeline import fast_stages as fast_stages


class Transaction:
    def __init__(self, db):
        self.db = db
        self.writes = []

    def set(self, ref, data, merge=False):
        self.writes.append((ref, dict(data), merge))


class TransactionDB(FakeClient):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def transaction(self):
        return Transaction(self)


@pytest.fixture
def transaction_db(monkeypatch):
    def transactional(fn):
        @wraps(fn)
        def invoke(tx):
            with tx.db.lock:
                result = fn(tx)
                for ref, data, merge in tx.writes:
                    ref.set(data, merge=merge)
                return result
        return invoke
    monkeypatch.setattr(bp.firestore, 'transactional', transactional)
    return TransactionDB()


def terminal_pages(db, count=2):
    _make_job(db, page_count=count)
    for number in range(1, count + 1):
        bp._page_ref(db, 'job1', number).set({
            'page_number': number, 'status': 'done', 'text': f'Story {number}', 'is_story': True,
        })


def test_simultaneous_claim_and_stale_owner_commit(transaction_db):
    db = transaction_db
    _make_job(db)
    barrier = threading.Barrier(3)
    claims = []
    def claim(owner):
        barrier.wait()
        claims.append((owner, bp._claim_page(db, 'job1', 1, owner)))
    threads = [threading.Thread(target=claim, args=(str(n),)) for n in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
    assert sum(result for _, result in claims) == 1
    owner = next(owner for owner, result in claims if result)
    page = bp._page_ref(db, 'job1', 1)
    page.set({'claimed_at': bp._now() - timedelta(seconds=bp._CLAIM_LEASE_SECONDS + 1)}, merge=True)
    assert bp._claim_page(db, 'job1', 1, 'replacement')
    fields = bp._result_to_fields('ok', ('late', True, 'story'), None, False)
    assert not bp._owned_page_write(db, 'job1', 1, owner, fields)
    assert bp._owned_page_write(db, 'job1', 1, 'replacement', fields)


def test_cancellation_prevents_page_result_commit(transaction_db):
    db = transaction_db
    _make_job(db)
    assert bp._claim_page(db, 'job1', 1, 'owner')
    bp.cancel_job(db, 'job1')
    assert not bp._owned_page_write(db, 'job1', 1, 'owner', {'status': 'done', 'text': 'late'})
    assert bp._page_ref(db, 'job1', 1).get().to_dict()['status'] == 'processing'


def test_character_claim_serializes_paid_call_and_cancellation(transaction_db, monkeypatch):
    db = transaction_db
    terminal_pages(db)
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def detector(pages, client, model=None):
        calls.append(pages)
        entered.set()
        assert finish.wait(3)
        return [{'name': 'Mouse', 'aliases': ['the little mouse']}]
    monkeypatch.setattr(bp, 'detect_book_characters', detector)
    worker = threading.Thread(target=bp._detect_characters, args=(db, 'job1', object(), SETTINGS))
    worker.start()
    assert entered.wait(3)
    bp._detect_characters(db, 'job1', object(), SETTINGS)
    assert len(calls) == 1
    bp.cancel_job(db, 'job1')
    finish.set()
    worker.join(timeout=3)
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['status'] == 'cancelled'
    assert 'character_suggestions' not in job


def test_character_commit_rejects_changed_text_revision(transaction_db, monkeypatch):
    db = transaction_db
    terminal_pages(db)
    def detector(pages, client, model=None):
        bp._page_ref(db, 'job1', 1).set({'text': 'changed after claim'}, merge=True)
        return [{'name': 'Stale'}]
    monkeypatch.setattr(bp, 'detect_book_characters', detector)
    bp._detect_characters(db, 'job1', object(), SETTINGS)
    assert bp._job_ref(db, 'job1').get().to_dict()['character_status'] == 'processing'
    bp._refresh_completion(db, 'job1')
    assert not bp._job_ref(db, 'job1').get().to_dict()['extraction_complete']


def test_empty_success_is_terminal_and_never_repeated(transaction_db, monkeypatch):
    db = transaction_db
    terminal_pages(db)
    calls = []
    monkeypatch.setattr(bp, 'detect_book_characters', lambda *a, **kw: calls.append(1) or [])
    bp._detect_characters(db, 'job1', object(), SETTINGS)
    bp._detect_characters(db, 'job1', object(), SETTINGS)
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert calls == [1]
    assert job['character_status'] == 'done'
    assert job['character_outcome'] == 'empty'
    assert job['character_suggestions'] == []


def test_incomplete_coverage_and_all_failed_are_distinct(transaction_db, monkeypatch):
    db = transaction_db
    terminal_pages(db)
    bp._page_ref(db, 'job1', 2).set({'status': 'failed'}, merge=True)
    inputs = []
    monkeypatch.setattr(bp, 'detect_book_characters', lambda pages, *a, **kw: inputs.append(pages) or [])
    bp._detect_characters(db, 'job1', object(), SETTINGS)
    bp._refresh_completion(db, 'job1')
    job = bp.get_job_progress(db, 'job1')
    assert inputs == [[(1, 'Story 1')]]
    assert job['status'] == 'partial'
    assert job['character_coverage'] == 'incomplete'
    assert job['extraction_complete']
    terminal_pages(db)
    for number in (1, 2):
        bp._page_ref(db, 'job1', number).set({'status': 'failed'}, merge=True)
    bp._detect_characters(db, 'job1', object(), SETTINGS)
    bp._refresh_completion(db, 'job1')
    job = bp.get_job_progress(db, 'job1')
    assert job['status'] == 'failed'
    assert job['character_outcome'] == 'no_text'


def test_job_creation_is_idempotent_and_settings_are_frozen(transaction_db, monkeypatch):
    db, fs = transaction_db, FakeFs()
    settings = dict(SETTINGS)
    link = bp.create_or_get_job(fs, db, object(), settings, job_id='b_g1',
        source_keys=['durable/1', 'durable/2'], entered_by='alice', book_id='Book', batch_id='b')
    settings['extraction_max_edge'] = 1000
    again = bp.create_or_get_job(fs, db, object(), settings, job_id='b_g1',
        source_keys=['durable/1', 'durable/2'], entered_by='alice', book_id='Book', batch_id='b')
    assert again == link
    assert bp._job_ref(db, 'b_g1').get().to_dict()['ai_settings']['extraction_max_edge'] == 2000
    with pytest.raises(ValueError, match='different input'):
        bp.create_or_get_job(fs, db, object(), settings, job_id='b_g1',
            source_keys=['durable/2', 'durable/1'], entered_by='alice', book_id='Book', batch_id='b')
    calls = []
    monkeypatch.setattr(bp, '_spawn_worker', lambda *args: calls.append(args))
    bp.resume_job(fs, db, object(), 'b_g1')
    assert calls[0][3]['extraction_max_edge'] == 2000
    assert calls[0][-1] is None


def test_missing_source_blocks_then_resumes_without_browser_bytes(fast_stages):
    db, fs = FakeClient(), FakeFs()
    bp.create_or_get_job(fs, db, object(), SETTINGS, job_id='b_g1',
        source_keys=['durable/1', 'durable/2'], entered_by='alice')
    fs.store['durable/1'] = b'one'
    prefix = bp._job_ref(db, 'b_g1').get().to_dict()['s3_prefix']
    bp._run_worker(fs, db, object(), SETTINGS, 'b_g1', prefix, 2, None, threading.Event())
    assert bp._job_ref(db, 'b_g1').get().to_dict()['status'] == 'blocked'
    assert bp._page_ref(db, 'b_g1', 1).get().to_dict()['raw_staged']
    assert bp._page_ref(db, 'b_g1', 2).get().to_dict().get('status') != 'failed'
    fs.store['durable/2'] = b'two'
    bp._run_worker(fs, db, object(), SETTINGS, 'b_g1', prefix, 2, None, threading.Event())
    assert bp._job_ref(db, 'b_g1').get().to_dict()['status'] == 'complete'


def test_ocr_failure_preserves_correction_and_orientation_flags(fast_stages, monkeypatch):
    db, fs = FakeClient(), FakeFs()
    _make_job(db, page_count=1)
    monkeypatch.setattr(bp, 'correct_page_image', lambda *a: (b'new', b'new', 'rotation', True))
    monkeypatch.setattr(uploader, 'attempt_page_extraction', lambda *a, **kw: ('error', ('OCR', 'bad reply')))
    bp._run_worker(fs, db, object(), SETTINGS, 'job1', 'sawimages/__pending__job1',
                   1, [b'raw'], threading.Event())
    result = bp._page_ref(db, 'job1', 1).get().to_dict()
    assert result['status'] == 'failed'
    assert result['corrected'] and result['rotation_uncertain']
    assert result['method'] == 'rotation'


def test_explicit_retry_invalidates_review_and_finalization(transaction_db):
    db = transaction_db
    terminal_pages(db)
    bp._page_ref(db, 'job1', 2).set({'status': 'failed', 'materialized_page': {'text': ''}}, merge=True)
    bp._job_ref(db, 'job1').set({'status': 'partial', 'extraction_complete': True,
        'finalization_status': 'complete', 'character_status': 'done',
        'character_revision': 'old', 'character_reviewed_revision': 'old'}, merge=True)
    assert bp.retry_failed_pages(db, 'job1')
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['character_status'] == 'pending'
    assert job['finalization_status'] == 'pending'
    assert job['character_review_stale']
    assert not job['extraction_complete']
    assert bp._page_ref(db, 'job1', 1).get().to_dict()['status'] == 'done'
    assert bp._page_ref(db, 'job1', 2).get().to_dict()['materialized_page'] == {'text': ''}


def test_scheduler_bounds_all_jobs_and_retains_no_images(monkeypatch):
    db, fs = FakeClient(), FakeFs()
    entered, finish = threading.Event(), threading.Event()
    state = {'active': 0, 'peak': 0}
    calls = []
    def run(*args):
        state['active'] += 1
        state['peak'] = max(state['peak'], state['active'])
        calls.append(args[4])
        assert args[-2] is None
        if len(calls) == 1:
            entered.set()
            assert finish.wait(3)
        state['active'] -= 1
    monkeypatch.setattr(bp, '_run_worker', run)
    for name in ('one', 'two'):
        bp.create_or_get_job(fs, db, object(), SETTINGS, job_id=name,
                            source_keys=['durable/1'], entered_by='alice')
    first = bp.resume_job(fs, db, object(), 'one')
    assert entered.wait(3)
    second = bp.resume_job(fs, db, object(), 'two')
    assert first['thread'] is second['thread']
    assert list(bp._WORK_QUEUE) == ['two']
    finish.set()
    first['thread'].join(timeout=3)
    assert calls == ['one', 'two']
    assert state['peak'] == 1
    assert not bp._ACTIVE_WORKERS


def test_worker_usage_uses_injected_raw_client_without_streamlit(monkeypatch):
    import utilities
    db = FakeClient()
    def forbidden(*args, **kwargs):
        raise AssertionError('Streamlit FirestoreWrapper must not be constructed')
    monkeypatch.setattr(utilities, 'FirestoreWrapper', forbidden)
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    with worker_runtime(object(), SETTINGS, db, 'job1', 'batch1'):
        utilities.record_api_usage(response, SETTINGS['extraction_model'], 'batch_ocr')
        assert utilities.get_ai_settings() == SETTINGS
    assert any(key.startswith('api_usage/') for key in db.store)
    assert 'usage' in db.store['pipeline_jobs/job1']
    assert 'usage' in db.store['photo_batches/batch1']


def test_stale_worker_cannot_publish_corrected_image(fast_stages, monkeypatch):
    db, fs = FakeClient(), FakeFs()
    _make_job(db, page_count=1)
    def stale_correction(*args):
        bp._page_ref(db, 'job1', 1).set({'claimed_by': 'new-owner'}, merge=True)
        return b'stale', b'stale', 'opencv', False
    monkeypatch.setattr(bp, 'correct_page_image', stale_correction)
    bp._run_worker(fs, db, object(), SETTINGS, 'job1', 'sawimages/__pending__job1',
                   1, [b'raw'], threading.Event())
    assert 'sawimages/__pending__job1/page_1_cropped.jpg' not in fs.store
    assert bp._job_ref(db, 'job1').get().to_dict()['status'] == 'waiting'


def test_artifact_publication_resumes_without_repeating_ocr(fast_stages, monkeypatch):
    class FlakyFs(FakeFs):
        fail = True
        def open(self, path, mode='rb'):
            if self.fail and path.endswith('/page_1_cropped.jpg') and 'w' in mode:
                self.fail = False
                raise OSError('temporary copy failure')
            return super().open(path, mode)
    db, fs = FakeClient(), FlakyFs()
    _make_job(db, page_count=1)
    calls = []
    monkeypatch.setattr(uploader, 'attempt_page_extraction',
                        lambda *a, **kw: calls.append(1) or ('ok', ('Story', True, 'story')))
    args = (fs, db, object(), SETTINGS, 'job1', 'sawimages/__pending__job1', 1)
    bp._run_worker(*args, [b'raw'], threading.Event())
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['status'] == 'blocked'
    assert bp._page_ref(db, 'job1', 1).get().to_dict()['artifacts_pending']
    bp._run_worker(*args, None, threading.Event())
    assert calls == [1]
    assert bp._job_ref(db, 'job1').get().to_dict()['status'] == 'complete'
    assert not bp._page_ref(db, 'job1', 1).get().to_dict()['artifacts_pending']


def test_scheduler_records_unexpected_failure_and_advances_next_book(monkeypatch):
    db, fs = FakeClient(), FakeFs()
    entered, finish = threading.Event(), threading.Event()
    completed = []
    def run(*args):
        if args[4] == 'broken':
            entered.set()
            assert finish.wait(3)
            raise AttributeError('unexpected decoder failure')
        completed.append(args[4])
    monkeypatch.setattr(bp, '_run_worker', run)
    for name in ('broken', 'healthy'):
        bp.create_or_get_job(fs, db, object(), SETTINGS, job_id=name,
                            source_keys=['durable/1'], entered_by='alice')
    first = bp.resume_job(fs, db, object(), 'broken')
    assert entered.wait(3)
    bp.resume_job(fs, db, object(), 'healthy')
    finish.set()
    first['thread'].join(timeout=3)
    assert completed == ['healthy']
    broken = bp._job_ref(db, 'broken').get().to_dict()
    assert broken['status'] == 'blocked'
    assert 'unexpected decoder failure' in broken['worker_error']


def test_remote_cancel_during_source_failure_cannot_be_overwritten(transaction_db):
    db = transaction_db
    remote = TransactionDB()
    remote.store, remote.lock = db.store, db.lock
    _make_job(db, page_count=1)
    local_cancel = threading.Event()
    class CancelThenFailFs(FakeFs):
        def exists(self, path):
            bp.cancel_job(remote, 'job1')
            raise OSError('source failure after another process cancelled')
    bp._run_worker(CancelThenFailFs(), db, object(), SETTINGS, 'job1',
                   'sawimages/__pending__job1', 1, [b'raw'], local_cancel)
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['status'] == 'cancelled'
    assert not local_cancel.is_set()
    assert not job['raw_uploaded']


@pytest.mark.parametrize('checkpoint', ['raw_uploaded', 'processing'])
def test_remote_cancel_at_stage_transition_cannot_revive_job(
    transaction_db, fast_stages, monkeypatch, checkpoint,
):
    db, fs = transaction_db, FakeFs()
    remote = TransactionDB()
    remote.store, remote.lock = db.store, db.lock
    _make_job(db, page_count=1)
    local_cancel = threading.Event()
    update = bp._set_job_if_active
    def cancel_before_update(client, job_id, fields):
        if ((checkpoint == 'raw_uploaded' and fields.get('raw_uploaded'))
                or (checkpoint == 'processing' and fields.get('status') == 'processing')):
            bp.cancel_job(remote, job_id)
        return update(client, job_id, fields)
    monkeypatch.setattr(bp, '_set_job_if_active', cancel_before_update)
    calls = []
    monkeypatch.setattr(uploader, 'attempt_page_extraction',
                        lambda *a, **kw: calls.append(1) or ('ok', ('late', True, 'story')))
    bp._run_worker(fs, db, object(), SETTINGS, 'job1', 'sawimages/__pending__job1',
                   1, [b'raw'], local_cancel)
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['status'] == 'cancelled'
    assert not local_cancel.is_set()
    assert calls == []
    assert bp._page_ref(db, 'job1', 1).get().to_dict().get('status') is None
    if checkpoint == 'raw_uploaded':
        assert not job['raw_uploaded']
