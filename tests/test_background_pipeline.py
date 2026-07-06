"""Tests for the background page-processing job (#179).

Exercises the job lifecycle — start / fingerprint matching / result consumption
/ character-suggestion precompute / cancellation — against monkeypatched stage
functions, so no OpenCV, network or Streamlit runtime is involved. The worker
runs on a real (daemon) thread exactly as in production; the session is a plain
dict, mirroring how ``st.session_state`` merely holds a reference to the job.
"""

import time

import pytest

import background_pipeline as bp
import pages.uploader as uploader


@pytest.fixture
def fast_stages(monkeypatch):
    """Replace the slow pipeline stages with instant fakes."""
    monkeypatch.setattr(bp, "exif_transpose_bytes", lambda raw: raw)
    monkeypatch.setattr(
        bp, "correct_page_image",
        lambda raw, client, settings, report=None: (raw, b"corrected-" + raw, "opencv"),
    )
    # The worker imports this lazily from pages.uploader at run time, so
    # patching the uploader module attribute is what it sees.
    monkeypatch.setattr(
        uploader, "attempt_page_extraction",
        lambda image_bytes, client, settings, label: (
            'ok', (image_bytes.decode(), True, "story"),
        ),
    )
    monkeypatch.setattr(
        bp, "detect_book_characters",
        lambda pages, client, progress_callback=None, model=None: [
            {"name": "Tom", "gender": "Male", "human": True,
             "plural": False, "protagonist": True, "aliases": ["the boy"]},
        ],
    )


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


def _wait_done(job, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not job['done'] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job['done'], "worker did not finish in time"


def test_job_processes_pages_and_precomputes_characters(fast_stages):
    session = {}
    photos = [b"page-1", b"page-2"]

    job = bp.start_page_processing_job(session, photos, object(), SETTINGS)
    _wait_done(job)

    # The consumer finds the job by fingerprint of the SAME bytes...
    assert bp.get_page_processing_job(session, photos) is job

    # ...and consumes each page's result in order (pop frees the entry).
    result_1 = bp.wait_for_page_result(job, 1)
    assert result_1['corrected'] == b"corrected-page-1"
    assert result_1['method'] == "opencv"
    assert result_1['extraction'] == ('ok', ("page-1", True, "story"))
    assert 1 not in job['results']

    result_2 = bp.wait_for_page_result(job, 2)
    assert result_2['extraction'] == ('ok', ("page-2", True, "story"))

    # Character detection ran last, over the extracted story text.
    assert bp.wait_for_character_suggestions(job) == [
        {"name": "Tom", "gender": "Male", "human": True,
         "plural": False, "protagonist": True, "aliases": ["the boy"]},
    ]

    bp.clear_page_processing_job(session)
    assert bp.get_page_processing_job(session, photos) is None


def test_start_is_idempotent_per_photo_set(fast_stages):
    session = {}
    photos = [b"page-1"]

    job_a = bp.start_page_processing_job(session, photos, object(), SETTINGS)
    job_b = bp.start_page_processing_job(session, photos, object(), SETTINGS)
    assert job_a is job_b
    _wait_done(job_a)


def test_different_photo_set_replaces_and_cancels_the_stale_job(fast_stages):
    session = {}
    job_a = bp.start_page_processing_job(session, [b"old-1"], object(), SETTINGS)
    job_b = bp.start_page_processing_job(session, [b"new-1"], object(), SETTINGS)
    assert job_b is not job_a
    assert job_a['cancelled'] is True
    # The old job no longer matches its own photos (superseded in the session).
    assert bp.get_page_processing_job(session, [b"old-1"]) is None
    assert bp.get_page_processing_job(session, [b"new-1"]) is job_b
    _wait_done(job_b)


def test_fingerprint_mismatch_returns_no_job(fast_stages):
    session = {}
    job = bp.start_page_processing_job(session, [b"page-1"], object(), SETTINGS)
    assert bp.get_page_processing_job(session, [b"other"]) is None
    _wait_done(job)


def test_cancel_drops_the_job_and_sets_the_flag(fast_stages):
    session = {}
    job = bp.start_page_processing_job(session, [b"page-1"], object(), SETTINGS)
    bp.cancel_page_processing_job(session)
    assert job['cancelled'] is True
    assert bp.JOB_STATE_KEY not in session
    # Safe when nothing is running.
    bp.cancel_page_processing_job(session)


def test_page_failure_is_contained_and_classified(fast_stages, monkeypatch):
    def _boom(raw, client, settings, report=None):
        if raw == b"page-2":
            raise RuntimeError("corrupt photo")
        return raw, None, None

    monkeypatch.setattr(bp, "correct_page_image", _boom)

    session = {}
    job = bp.start_page_processing_job(
        session, [b"page-1", b"page-2"], object(), SETTINGS
    )
    _wait_done(job)

    assert bp.wait_for_page_result(job, 1)['extraction'][0] == 'ok'
    outcome, (error_type, error_message) = bp.wait_for_page_result(job, 2)['extraction']
    assert outcome == 'error'
    assert error_type == "RuntimeError"
    assert "corrupt photo" in error_message
    # Detection still ran over the surviving story page.
    assert bp.wait_for_character_suggestions(job) is not None


def test_detection_failure_leaves_suggestions_none(fast_stages, monkeypatch):
    def _det_boom(*args, **kwargs):
        raise ValueError("bad json")

    monkeypatch.setattr(bp, "detect_book_characters", _det_boom)

    session = {}
    job = bp.start_page_processing_job(session, [b"page-1"], object(), SETTINGS)
    _wait_done(job)
    assert bp.wait_for_page_result(job, 1) is not None
    assert bp.wait_for_character_suggestions(job) is None
    assert "bad json" in job['character_error']
