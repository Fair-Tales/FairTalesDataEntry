"""Tests for #181 — the orientation-check invariant in ``correct_page_image``.

While rotation correction is enabled, a saved corrected image must NEVER skip
the dedicated rotation check: not on the high-confidence OpenCV fast path, not
when the crop-quality gate approves a crop (the last remaining hole — the
gate's single yes/no answer was trusted for orientation and let 180° pages
through), and not with the gate disabled. Pure monkeypatched-unit tests, no
network or Streamlit.
"""

import pytest

import image_processing as ip
from utilities import AI_SETTINGS_DEFAULTS

RAW = b"raw-bytes"
CROP = b"cropped-bytes"


@pytest.fixture
def settings():
    return dict(AI_SETTINGS_DEFAULTS)


@pytest.fixture
def calls(monkeypatch):
    """Instrument the pipeline's collaborators; returns the call recorder."""
    record = {"rotation_checked_on": [], "rotated": []}

    def fake_rotation(image_bytes, client, model=None):
        record["rotation_checked_on"].append(image_bytes)
        return record.get("angle", 0), record.get("uncertain", False)

    def fake_rotate(image_bytes, angle):
        record["rotated"].append((image_bytes, angle))
        return f"rotated-{angle}".encode()

    monkeypatch.setattr(ip, "get_rotation_angle", fake_rotation)
    monkeypatch.setattr(ip, "rotate_image", fake_rotate)
    return record


def _stub_opencv(monkeypatch, ok, high_confidence):
    monkeypatch.setattr(
        ip, "correct_book_page", lambda raw: (CROP if ok else None, ok, high_confidence)
    )


def test_high_confidence_path_checks_and_fixes_rotation(calls, settings, monkeypatch):
    _stub_opencv(monkeypatch, ok=True, high_confidence=True)
    calls["angle"] = 180
    for_extraction, corrected, method, uncertain = ip.correct_page_image(RAW, object(), settings)
    assert calls["rotation_checked_on"] == [CROP]
    assert calls["rotated"] == [(CROP, 180)]
    assert corrected == b"rotated-180" and for_extraction == b"rotated-180"
    assert method == "opencv"


def test_gate_approved_path_still_checks_rotation(calls, settings, monkeypatch):
    """THE #181 fix: a crop the quality gate approves must still get the
    dedicated 0/90/180/270 check — the gate's combined yes/no was letting
    upside-down pages through uncorrected."""
    _stub_opencv(monkeypatch, ok=True, high_confidence=False)
    monkeypatch.setattr(ip, "check_crop_quality", lambda *a, **k: True)
    calls["angle"] = 180
    for_extraction, corrected, method, uncertain = ip.correct_page_image(RAW, object(), settings)
    assert calls["rotation_checked_on"] == [CROP]
    assert corrected == b"rotated-180"
    assert method == "opencv"


def test_gate_off_path_checks_rotation(calls, settings, monkeypatch):
    settings["enable_crop_quality_gate"] = False
    _stub_opencv(monkeypatch, ok=True, high_confidence=False)
    calls["angle"] = 90
    _, corrected, method, uncertain = ip.correct_page_image(RAW, object(), settings)
    assert calls["rotation_checked_on"] == [CROP]
    assert corrected == b"rotated-90"
    assert method == "opencv"


def test_upright_gate_approved_crop_is_untouched(calls, settings, monkeypatch):
    _stub_opencv(monkeypatch, ok=True, high_confidence=False)
    monkeypatch.setattr(ip, "check_crop_quality", lambda *a, **k: True)
    calls["angle"] = 0
    _, corrected, method, uncertain = ip.correct_page_image(RAW, object(), settings)
    assert calls["rotation_checked_on"] == [CROP]
    assert calls["rotated"] == []
    assert corrected == CROP


def test_gate_rejected_falls_back_to_rotating_the_raw(calls, settings, monkeypatch):
    _stub_opencv(monkeypatch, ok=True, high_confidence=False)
    monkeypatch.setattr(ip, "check_crop_quality", lambda *a, **k: False)
    calls["angle"] = 180
    for_extraction, corrected, method, uncertain = ip.correct_page_image(RAW, object(), settings)
    assert calls["rotation_checked_on"] == [RAW]
    assert corrected == b"rotated-180"
    assert method == "rotation"


def test_rotation_disabled_never_calls_the_check(calls, settings, monkeypatch):
    settings["enable_rotation_correction"] = False
    _stub_opencv(monkeypatch, ok=True, high_confidence=False)
    monkeypatch.setattr(ip, "check_crop_quality", lambda *a, **k: True)
    _, corrected, method, uncertain = ip.correct_page_image(RAW, object(), settings)
    assert calls["rotation_checked_on"] == []
    assert corrected == CROP
