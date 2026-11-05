"""Reconstruction retains correction evidence even when subsequent OCR fails."""

import io
from types import SimpleNamespace

import pytest

import book_reconstruction as reconstruction


@pytest.mark.parametrize('ocr_fails', [False, True])
def test_reconstruction_correction_fields_survive_ocr(monkeypatch, ocr_fails):
    stored = {}

    class Page:
        def __init__(self, page_number, book):
            self.page_number = page_number
            self.text = ''
            self.contains_story = False
            self.corrected = None
            self.rotation_uncertain = False

        def register(self):
            stored[self.page_number] = dict(vars(self))

    def extract(*args, **kwargs):
        if ocr_fails:
            raise reconstruction.PageExtractionError('AnthropicError', 'offline fixture')
        return 'Story text.', True, 'story'

    monkeypatch.setattr(reconstruction, 'Page', Page)
    monkeypatch.setattr(reconstruction, 'exif_transpose_bytes', lambda data: data)
    monkeypatch.setattr(reconstruction, '_process_page', lambda *args: (b'corrected', 'opencv', True))
    monkeypatch.setattr(reconstruction, 'extract_page_info', extract)
    book = SimpleNamespace(title='Offline book', get_ref=lambda: object())
    fs = SimpleNamespace(open=lambda *args: io.BytesIO())

    pages, failed = reconstruction._process_pages(
        book, [('page.jpg', b'photo')], object(), fs, None,
    )

    assert stored[1]['corrected'] is True
    assert stored[1]['rotation_uncertain'] is True
    assert pages[1].text == ('' if ocr_fails else 'Story text.')
    assert failed == ([1] if ocr_fails else [])
