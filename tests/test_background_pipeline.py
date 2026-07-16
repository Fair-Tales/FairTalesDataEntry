"""Tests for the DURABLE background page-processing job (#179).

The job persists its state to Firestore (``pipeline_jobs`` + a ``pages``
subcollection) and its page images to S3 as it goes, so the work survives a
disconnect and costs no RAM. These tests exercise that against in-memory fakes
for the firestore client and the s3fs filesystem — no network, no Streamlit
runtime, no real thread (the worker body is driven synchronously for
determinism). Concurrency (the transactional page claim) is covered by the pure
``_should_claim`` decision test.
"""

import io
import threading

import pytest

import background_pipeline as bp
import pages.uploader as uploader


# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------

class _WBuf(io.BytesIO):
    def __init__(self, store, path):
        super().__init__()
        self._store = store
        self._path = path

    def __exit__(self, *exc):
        self._store[self._path] = self.getvalue()
        return False


class FakeFs:
    """Minimal s3fs stand-in: a flat path -> bytes store."""

    def __init__(self):
        self.store = {}

    def open(self, path, mode='rb'):
        if 'w' in mode:
            return _WBuf(self.store, path)
        return io.BytesIO(self.store[path])

    def exists(self, path):
        p = path.rstrip('/')
        return p in self.store or any(k.startswith(p + '/') for k in self.store)

    def ls(self, path, detail=False):
        p = path.rstrip('/') + '/'
        return [k for k in self.store if k.startswith(p)]

    def mv(self, src, dst):
        self.store[dst] = self.store.pop(src)

    def rm(self, path, recursive=False):
        p = path.rstrip('/')
        for k in [k for k in self.store if k == p or k.startswith(p + '/')]:
            self.store.pop(k, None)


class FakeSnap:
    def __init__(self, path, data):
        self.id = path.rsplit('/', 1)[-1]
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDoc:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def get(self, transaction=None):
        return FakeSnap(self._path, self._store.get(self._path))

    def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path] = {**self._store[self._path], **data}
        else:
            self._store[self._path] = dict(data)

    def collection(self, name):
        return FakeCollection(self._store, self._path + '/' + name)


class FakeCollection:
    def __init__(self, store, prefix):
        self._store = store
        self._prefix = prefix

    def document(self, doc_id):
        return FakeDoc(self._store, self._prefix + '/' + str(doc_id))

    def stream(self):
        base = self._prefix + '/'
        for path, data in list(self._store.items()):
            if path.startswith(base) and '/' not in path[len(base):]:
                yield FakeSnap(path, data)


class FakeClient:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, name)


SETTINGS = {
    'extraction_model': 'claude-sonnet-5',
    'extraction_max_edge': 2000,
    'extraction_max_tokens': 2048,
    'character_detection_model': 'claude-sonnet-5',
    'crop_quality_model': 'claude-haiku-4-5',
    'rotation_model': 'claude-sonnet-4-6',
    'enable_rotation_correction': True,
    'enable_crop_quality_gate': True,
}


@pytest.fixture
def fast_stages(monkeypatch):
    """Instant fakes for the slow stages; a fake page-claim that skips the real
    firestore transaction."""
    monkeypatch.setattr(bp, 'exif_transpose_bytes', lambda raw: raw)
    monkeypatch.setattr(
        bp, 'correct_page_image',
        lambda raw, client, settings, report=None: (raw, b'corrected-' + raw, 'opencv', False),
    )
    monkeypatch.setattr(
        uploader, 'attempt_page_extraction',
        lambda image_bytes, client, settings, label: ('ok', (image_bytes.decode(), True, 'story')),
    )
    monkeypatch.setattr(
        bp, 'detect_book_characters',
        lambda pages, client, progress_callback=None, model=None: [{'name': 'Tom'}],
    )

    def _fake_claim(db, job_id, page_number, worker_id):
        page_ref = bp._page_ref(db, job_id, page_number)
        snap = page_ref.get()
        if not bp._should_claim(snap.to_dict() if snap.exists else None):
            return False
        page_ref.set({'page_number': page_number, 'status': 'processing'}, merge=True)
        return True

    monkeypatch.setattr(bp, '_claim_page', _fake_claim)


def _make_job(db, job_id='job1', s3_prefix='sawimages/__pending__job1', page_count=2):
    bp._job_ref(db, job_id).set({
        'job_id': job_id, 'page_count': page_count, 's3_prefix': s3_prefix,
        'raw_uploaded': False, 'status': 'processing', 'character_status': 'pending',
    })
    return job_id


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------

def test_result_field_roundtrip_ok():
    fields = bp._result_to_fields('ok', ('hello', True, 'story'), 'opencv', True)
    assert fields['status'] == 'done'
    assert bp._fields_to_result(fields) == (
        'ok', ('hello', True, 'story'), 'opencv', True, False
    )


def test_result_field_roundtrip_error():
    fields = bp._result_to_fields('error', ('AnthropicError', 'boom'), None, False)
    assert fields['status'] == 'failed'
    assert bp._fields_to_result(fields) == (
        'error', ('AnthropicError', 'boom'), None, False, False
    )


def test_result_field_roundtrip_rotation_uncertain():
    """The #217 uncertainty flag survives the durable-result round trip, and a
    pre-#217 persisted result (no key) reads back as not-uncertain."""
    fields = bp._result_to_fields('ok', ('hello', True, 'story'), 'opencv', True, True)
    assert fields['rotation_uncertain'] is True
    assert bp._fields_to_result(fields) == (
        'ok', ('hello', True, 'story'), 'opencv', True, True
    )
    legacy = dict(fields)
    del legacy['rotation_uncertain']
    assert bp._fields_to_result(legacy)[4] is False


def test_fields_to_result_pending_is_none():
    assert bp._fields_to_result({'status': 'processing'}) is None
    assert bp._fields_to_result({}) is None


def test_should_claim_decisions():
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    assert bp._should_claim(None, now) is True
    assert bp._should_claim({'status': 'pending'}, now) is True
    assert bp._should_claim({'status': 'done'}, now) is False
    assert bp._should_claim({'status': 'failed'}, now) is False
    fresh = now - timedelta(seconds=5)
    assert bp._should_claim({'status': 'processing', 'claimed_at': fresh}, now) is False
    stale = now - timedelta(seconds=bp._CLAIM_LEASE_SECONDS + 5)
    assert bp._should_claim({'status': 'processing', 'claimed_at': stale}, now) is True


def test_placeholder_and_prefix_helpers():
    assert bp._placeholder_title('abc') == '__pending__abc'
    assert bp._s3_prefix_for('The Gruffalo') == 'sawimages/The Gruffalo'


# ---------------------------------------------------------------------------
# Worker body (driven synchronously).
# ---------------------------------------------------------------------------

def test_worker_processes_pages_to_s3_and_firestore(fast_stages):
    db = FakeClient()
    fs = FakeFs()
    job_id = _make_job(db)
    cancel = threading.Event()

    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   2, [b'page-1', b'page-2'], cancel)

    # Raw + corrected images written to the working prefix.
    assert fs.store['sawimages/__pending__job1/page_1.jpg'] == b'page-1'
    assert fs.store['sawimages/__pending__job1/page_1_cropped.jpg'] == b'corrected-page-1'
    assert fs.store['sawimages/__pending__job1/page_2_cropped.jpg'] == b'corrected-page-2'

    # Per-page results persisted.
    link = {'job_id': job_id}
    assert bp.wait_for_page_result(db, link, 1, worker_alive=lambda: False) == (
        'ok', ('page-1', True, 'story'), 'opencv', True, False,
    )
    # Parent doc: character detection + status complete.
    job_doc = db.store['pipeline_jobs/job1']
    assert job_doc['status'] == 'complete'
    assert job_doc['character_status'] == 'done'
    assert bp.wait_for_character_suggestions(db, link, worker_alive=lambda: False) == [{'name': 'Tom'}]


def test_worker_resumes_reading_raw_from_s3_when_no_bytes(fast_stages):
    """A resumed worker with no in-memory bytes reads each raw page back from S3
    (written by the first run's raw-upload phase) and finishes pending pages."""
    db = FakeClient()
    fs = FakeFs()
    job_id = _make_job(db)
    # Simulate the raw-upload phase already done and page 1 finished; page 2 left.
    fs.store['sawimages/__pending__job1/page_1.jpg'] = b'page-1'
    fs.store['sawimages/__pending__job1/page_2.jpg'] = b'page-2'
    bp._job_ref(db, job_id).set({'raw_uploaded': True}, merge=True)
    bp._page_ref(db, job_id, 1).set(
        bp._result_to_fields('ok', ('page-1', True, 'story'), 'opencv', True), merge=True)

    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   2, None, threading.Event())

    # Page 2 got processed from the S3 raw; page 1 untouched (already done).
    assert fs.store['sawimages/__pending__job1/page_2_cropped.jpg'] == b'corrected-page-2'
    link = {'job_id': job_id}
    assert bp.wait_for_page_result(db, link, 2, worker_alive=lambda: False)[1][0] == 'page-2'


def test_worker_page_failure_recorded_as_failed(fast_stages, monkeypatch):
    def _boom(raw, client, settings, report=None):
        if raw == b'page-2':
            raise RuntimeError('corrupt photo')
        return raw, b'corrected-' + raw, 'opencv', False

    monkeypatch.setattr(bp, 'correct_page_image', _boom)
    db = FakeClient()
    fs = FakeFs()
    job_id = _make_job(db)

    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   2, [b'page-1', b'page-2'], threading.Event())

    link = {'job_id': job_id}
    assert bp.wait_for_page_result(db, link, 1, worker_alive=lambda: False)[0] == 'ok'
    outcome, (etype, emsg), _m, _c, _u = bp.wait_for_page_result(db, link, 2, worker_alive=lambda: False)
    assert outcome == 'error'
    assert etype == 'RuntimeError' and 'corrupt photo' in emsg


def test_worker_stops_when_cancelled(fast_stages):
    db = FakeClient()
    fs = FakeFs()
    job_id = _make_job(db, page_count=3)
    cancel = threading.Event()
    cancel.set()

    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   3, [b'p1', b'p2', b'p3'], cancel)

    # Cancelled before any page: no page docs, status not advanced to complete.
    assert db.store['pipeline_jobs/job1']['status'] != 'complete'
    assert 'pipeline_jobs/job1/pages/1' not in db.store


def test_detection_skipped_when_no_story_text(fast_stages, monkeypatch):
    monkeypatch.setattr(
        uploader, 'attempt_page_extraction',
        lambda image_bytes, client, settings, label: ('ok', ('', False, 'blank')),
    )
    db = FakeClient()
    fs = FakeFs()
    job_id = _make_job(db)

    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   2, [b'p1', b'p2'], threading.Event())

    assert db.store['pipeline_jobs/job1']['character_status'] == 'skipped'
    link = {'job_id': job_id}
    assert bp.wait_for_character_suggestions(db, link, worker_alive=lambda: False) is None


# ---------------------------------------------------------------------------
# Job creation / lifecycle / consume helpers.
# ---------------------------------------------------------------------------

def test_start_creates_durable_job_with_extracted_title(monkeypatch):
    monkeypatch.setattr(bp, '_spawn_worker', lambda *a, **k: None)
    db = FakeClient()
    fs = FakeFs()
    session = {}

    link = bp.start_page_processing_job(
        session, fs, db, object(), SETTINGS, [b'p1', b'p2'],
        entered_by='alice', extracted_title='The Gruffalo',
    )
    assert link['working_title'] == 'The Gruffalo'
    assert link['s3_prefix'] == 'sawimages/The Gruffalo'
    doc = db.store[f"pipeline_jobs/{link['job_id']}"]
    assert doc['status'] == 'processing'
    assert doc['title_source'] == 'extracted'
    assert doc['entered_by'] == 'alice'
    assert doc['page_count'] == 2
    assert session[bp.JOB_STATE_KEY] is link


def test_start_uses_placeholder_when_title_folder_exists(monkeypatch):
    monkeypatch.setattr(bp, '_spawn_worker', lambda *a, **k: None)
    db = FakeClient()
    fs = FakeFs()
    fs.store['sawimages/The Gruffalo/page_1.jpg'] = b'existing'  # collision
    session = {}

    link = bp.start_page_processing_job(
        session, fs, db, object(), SETTINGS, [b'p1'],
        entered_by='alice', extracted_title='The Gruffalo',
    )
    assert link['working_title'].startswith('__pending__')
    assert db.store[f"pipeline_jobs/{link['job_id']}"]['title_source'] == 'placeholder'


def test_start_placeholder_when_no_title(monkeypatch):
    monkeypatch.setattr(bp, '_spawn_worker', lambda *a, **k: None)
    db = FakeClient()
    session = {}
    link = bp.start_page_processing_job(
        session, FakeFs(), db, object(), SETTINGS, [b'p1'],
        entered_by='bob', extracted_title=None,
    )
    assert link['working_title'].startswith('__pending__')


def test_start_is_idempotent_per_photo_set(monkeypatch):
    calls = []
    monkeypatch.setattr(bp, '_spawn_worker', lambda *a, **k: calls.append(1))
    db = FakeClient()
    fs = FakeFs()
    session = {}
    photos = [b'p1', b'p2']

    link_a = bp.start_page_processing_job(
        session, fs, db, object(), SETTINGS, photos, entered_by='a', extracted_title='T')
    link_b = bp.start_page_processing_job(
        session, fs, db, object(), SETTINGS, photos, entered_by='a', extracted_title='T')
    assert link_a is link_b
    # Only one job doc created; second call just re-ensured the worker.
    assert sum(1 for k in db.store
               if k.startswith('pipeline_jobs/') and '/' not in k[len('pipeline_jobs/'):]) == 1


def test_get_active_job_matches_and_rejects_finalized(monkeypatch):
    monkeypatch.setattr(bp, '_spawn_worker', lambda *a, **k: None)
    db = FakeClient()
    fs = FakeFs()
    session = {}
    photos = [b'p1']
    link = bp.start_page_processing_job(
        session, fs, db, object(), SETTINGS, photos, entered_by='a', extracted_title='T')

    assert bp.get_active_job(session, db, photos) is link
    assert bp.get_active_job(session, db, [b'different']) is None  # fingerprint mismatch

    bp._job_ref(db, link['job_id']).set({'status': 'finalized'}, merge=True)
    assert bp.get_active_job(session, db, photos) is None


def test_cancel_marks_job_cancelled(monkeypatch):
    monkeypatch.setattr(bp, '_spawn_worker', lambda *a, **k: None)
    db = FakeClient()
    session = {}
    link = bp.start_page_processing_job(
        session, FakeFs(), db, object(), SETTINGS, [b'p1'], entered_by='a', extracted_title='T')
    job_id = link['job_id']

    bp.cancel_page_processing_job(session, db)
    assert bp.JOB_STATE_KEY not in session
    assert db.store[f'pipeline_jobs/{job_id}']['status'] == 'cancelled'
    # Safe when nothing is running.
    bp.cancel_page_processing_job(session, db)


def test_wait_for_page_result_returns_none_when_no_worker_and_unfinished():
    db = FakeClient()
    _make_job(db)
    bp._page_ref(db, 'job1', 1).set({'status': 'processing'}, merge=True)
    link = {'job_id': 'job1'}
    assert bp.wait_for_page_result(db, link, 1, worker_alive=lambda: False) is None


# ---------------------------------------------------------------------------
# S3 title reconciliation (#179 title-edit case).
# ---------------------------------------------------------------------------

def test_reconcile_moves_working_prefix_to_final_title():
    fs = FakeFs()
    fs.store['sawimages/__pending__job1/page_1.jpg'] = b'r1'
    fs.store['sawimages/__pending__job1/page_1_cropped.jpg'] = b'c1'
    link = {'job_id': 'job1', 's3_prefix': 'sawimages/__pending__job1'}

    final = bp.reconcile_s3_prefix(fs, link, 'My Book')

    assert final == 'sawimages/My Book'
    assert fs.store['sawimages/My Book/page_1.jpg'] == b'r1'
    assert fs.store['sawimages/My Book/page_1_cropped.jpg'] == b'c1'
    assert not fs.exists('sawimages/__pending__job1')
    assert link['s3_prefix'] == 'sawimages/My Book'  # link updated for later writes


def test_reconcile_is_noop_when_titles_match():
    fs = FakeFs()
    fs.store['sawimages/My Book/page_1.jpg'] = b'r1'
    link = {'job_id': 'job1', 's3_prefix': 'sawimages/My Book'}

    final = bp.reconcile_s3_prefix(fs, link, 'My Book')
    assert final == 'sawimages/My Book'
    assert fs.store['sawimages/My Book/page_1.jpg'] == b'r1'  # untouched


def test_reconcile_tolerates_empty_working_prefix():
    fs = FakeFs()  # nothing written yet (all pages fell back)
    link = {'job_id': 'job1', 's3_prefix': 'sawimages/__pending__job1'}
    assert bp.reconcile_s3_prefix(fs, link, 'My Book') == 'sawimages/My Book'


# ---------------------------------------------------------------------------
# Two-worker character-detection race (#201): Phase C must never compute the
# book's characters over PARTIAL story text. Phase B `continue`s past pages
# freshly claimed by another (overlapping) worker, so a fast worker could
# previously reach Phase C while those pages were still processing, run
# detection over the incomplete text, and stamp character_status='done' —
# permanently blocking recomputation (the review form then silently missed
# characters). Now detection only runs once EVERY page is terminal.
# ---------------------------------------------------------------------------

def test_pages_all_terminal_helper():
    from datetime import datetime, timezone

    db = FakeClient()
    job_id = _make_job(db)

    # No page docs at all: not terminal.
    assert bp._pages_all_terminal(db, job_id, 2) is False

    # Page 1 done, page 2 missing: not terminal.
    bp._page_ref(db, job_id, 1).set({'page_number': 1, 'status': 'done'}, merge=True)
    assert bp._pages_all_terminal(db, job_id, 2) is False

    # Page 2 processing (claimed by another worker): not terminal.
    bp._page_ref(db, job_id, 2).set(
        {'page_number': 2, 'status': 'processing',
         'claimed_at': datetime.now(timezone.utc)},
        merge=True,
    )
    assert bp._pages_all_terminal(db, job_id, 2) is False

    # A FAILED page is terminal (it will register blank; the book's remaining
    # story text is final), as is done.
    bp._page_ref(db, job_id, 2).set({'status': 'failed'}, merge=True)
    assert bp._pages_all_terminal(db, job_id, 2) is True


def test_worker_defers_detection_while_another_worker_owns_a_page(fast_stages, monkeypatch):
    from datetime import datetime, timezone

    db = FakeClient()
    fs = FakeFs()
    job_id = _make_job(db)

    detection_runs = []
    monkeypatch.setattr(
        bp, 'detect_book_characters',
        lambda pages, client, progress_callback=None, model=None: (
            detection_runs.append(list(pages)) or [{'name': 'Tom'}]
        ),
    )

    # Page 2 is FRESH-claimed by another worker (not a stale lease), so this
    # worker's Phase B skips it and reaches Phase C with page 2 unfinished.
    bp._page_ref(db, job_id, 2).set(
        {'page_number': 2, 'status': 'processing',
         'claimed_by': 'other-worker', 'claimed_at': datetime.now(timezone.utc)},
        merge=True,
    )

    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   2, [b'page-1', b'page-2'], threading.Event())

    # Detection was NOT computed over the partial (page-1-only) text, and the
    # status is still pending so a later worker / the consumer fallback can
    # compute it over the full book.
    assert detection_runs == []
    assert db.store['pipeline_jobs/job1']['character_status'] == 'pending'

    # The other worker finishes page 2; a resumed worker (no in-memory bytes,
    # nothing left to claim) now finds every page terminal and runs detection
    # over the COMPLETE story text.
    bp._page_ref(db, job_id, 2).set(
        bp._result_to_fields('ok', ('page-2', True, 'story'), None, False), merge=True,
    )
    bp._run_worker(fs, db, object(), SETTINGS, job_id, 'sawimages/__pending__job1',
                   2, None, threading.Event())

    assert len(detection_runs) == 1
    assert [text for _n, text in detection_runs[0]] == ['page-1', 'page-2']
    job_doc = db.store['pipeline_jobs/job1']
    assert job_doc['character_status'] == 'done'
    # Diagnostic (#201): the number of story pages that fed the run is recorded
    # so a partial computation would be detectable after the fact.
    assert job_doc['character_pages_used'] == 2
