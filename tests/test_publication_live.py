"""Late metadata work runs on upload/worker paths; confirmed values survive."""
from types import SimpleNamespace
from datetime import timedelta

import background_pipeline as bp
import pages.uploader as uploader
import utilities
from test_background_pipeline import FakeFs, _make_job
from test_background_pipeline_durable import transaction_db as transaction_db


def test_upload_reconciliation_writes_only_separate_suggestions(monkeypatch):
    book = SimpleNamespace(title='Student title', author='student-author', publisher='student-publisher',
                           published=1980, metadata_evidence_payloads=[{'title': {'value': 'AI title'}}],
                           metadata_reconciliation=None)
    calls = []
    def reconcile(seed, pages, client, **kwargs):
        calls.append((seed, pages, kwargs))
        return {'title': 'New guess', 'authors': ['Different'], 'metadata_finalization': {'status': 'completed'}}
    monkeypatch.setattr(utilities, 'finalize_photo_metadata', reconcile)
    uploader._reconcile_uploaded_metadata(book, [{'page_number': 2, 'text': 'credits'}], {2: b'photo'}, object())
    assert book.title == 'Student title'
    assert book.author == 'student-author' and book.publisher == 'student-publisher'
    assert book.published == 1980
    assert book.metadata_reconciliation['title'] == 'New guess'
    uploader._reconcile_uploaded_metadata(book, [], {}, object())
    assert len(calls) == 1


def test_worker_claims_one_late_pass_and_retains_failed_raw(transaction_db, monkeypatch):
    db = transaction_db
    _make_job(db, page_count=1)
    bp._job_ref(db, 'job1').set({'book_id': 'book'}, merge=True)
    db.collection('books').document('book').set({
        'title': 'Student title', 'metadata_evidence_payloads': [{'credits': []}],
    })
    bp._page_ref(db, 'job1', 1).set({'status': 'done', 'text': 'copyright', 'is_story': False})
    fs = FakeFs()
    with fs.open('sawimages/__pending__job1/page_1.jpg', 'wb') as handle:
        handle.write(b'photo')
    calls = []
    def finalize(seed, pages, client, **kwargs):
        calls.append(pages)
        return {'metadata_finalization': {'status': 'failed'},
                'metadata_fallback_response': {'status': 'invalid_response', 'raw': '  broken JSON\n'}}
    monkeypatch.setattr(utilities, 'finalize_photo_metadata', finalize)
    bp._reconcile_job_metadata(fs, db, 'job1', object())
    bp._reconcile_job_metadata(fs, db, 'job1', object())
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert len(calls) == 1
    assert job['metadata_reconciliation_status'] == 'failed'
    assert job['metadata_reconciliation']['metadata_fallback_response']['raw'] == '  broken JSON\n'
    assert db.collection('books').document('book').get().to_dict()['title'] == 'Student title'


def _seed_metadata_job(db):
    _make_job(db, page_count=1)
    bp._job_ref(db, 'job1').set({'book_id': 'book', 'character_status': 'done'}, merge=True)
    db.collection('books').document('book').set({'metadata_evidence_payloads': [{'credits': []}]})
    bp._page_ref(db, 'job1', 1).set({'page_number': 1, 'status': 'done', 'text': 'copyright'})
    fs = FakeFs()
    with fs.open('sawimages/__pending__job1/page_1.jpg', 'wb') as handle:
        handle.write(b'photo')
    return fs


def test_expired_dispatched_metadata_lease_fails_closed_without_repeat(transaction_db, monkeypatch):
    db = transaction_db
    fs = _seed_metadata_job(db)
    bp._job_ref(db, 'job1').set({
        'metadata_reconciliation_status': 'processing',
        'metadata_reconciliation_claimed_at': bp._now() - timedelta(seconds=bp._CLAIM_LEASE_SECONDS + 1),
        'metadata_reconciliation_dispatch_started': True,
    }, merge=True)
    monkeypatch.setattr(utilities, 'finalize_photo_metadata', lambda *a, **k: (_ for _ in ()).throw(AssertionError('repeat spend')))
    bp._reconcile_job_metadata(fs, db, 'job1', object())
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['metadata_reconciliation_status'] == 'failed'
    assert job['metadata_reconciliation_error'] == 'dispatch_outcome_unknown_after_expired_lease'


def test_stale_revision_result_is_rejected_and_processing_blocks_completion(transaction_db, monkeypatch):
    db = transaction_db
    fs = _seed_metadata_job(db)
    def stale_result(*args, **kwargs):
        bp._page_ref(db, 'job1', 1).set({'text': 'new OCR revision'}, merge=True)
        return {'metadata_finalization': {'status': 'completed'}}
    monkeypatch.setattr(utilities, 'finalize_photo_metadata', stale_result)
    bp._reconcile_job_metadata(fs, db, 'job1', object())
    bp._refresh_completion(db, 'job1')
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert 'metadata_reconciliation' not in job
    assert not job['extraction_complete']


def test_expired_undispatched_metadata_lease_can_be_reclaimed(transaction_db, monkeypatch):
    db = transaction_db
    fs = _seed_metadata_job(db)
    bp._job_ref(db, 'job1').set({
        'metadata_reconciliation_status': 'processing', 'metadata_reconciliation_claimed_by': 'dead-worker',
        'metadata_reconciliation_claimed_at': bp._now() - timedelta(seconds=bp._CLAIM_LEASE_SECONDS + 1),
        'metadata_reconciliation_dispatch_started': False,
    }, merge=True)
    monkeypatch.setattr(utilities, 'finalize_photo_metadata', lambda *a, **k:
                        {'metadata_finalization': {'status': 'completed'}})
    bp._reconcile_job_metadata(fs, db, 'job1', object())
    job = bp._job_ref(db, 'job1').get().to_dict()
    assert job['metadata_reconciliation_status'] == 'done'
    assert job['metadata_reconciliation_claimed_by'] != 'dead-worker'
