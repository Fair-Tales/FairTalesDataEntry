"""Regression lock: every ``BookPhotoEntry.<attr>`` referenced by the photo-upload
code must actually be defined on the class.

A missing attribute here is not a soft failure — the QR-upload watcher fragment
(``add_book_photos._auto_upload_watcher`` / ``qr_landing._uploaded_list``) calls
``render_uploaded_photos_list`` on every poll, so a referenced-but-undefined text
string raises ``AttributeError`` on a live mid-upload render and crashes the page.
This bit production for ``uploaded_gaps_warning`` (#199 gap guard shipped, its
text string did not), which fires the moment a page slot is briefly missing —
i.e. almost always mid-upload.
"""

import re
from pathlib import Path

from text_content.forms import BookPhotoEntry

_SOURCES = [
    "photo_upload.py",
    "pages/add_book_photos.py",
    "pages/qr_landing.py",
    "pages/page_photo_upload.py",
]
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _referenced_attrs():
    refs = set()
    for rel in _SOURCES:
        path = _REPO_ROOT / rel
        if not path.exists():
            continue
        for match in re.finditer(r"BookPhotoEntry\.([A-Za-z_]\w*)", path.read_text()):
            refs.add(match.group(1))
    return refs


def test_all_referenced_book_photo_entry_attrs_exist():
    missing = sorted(a for a in _referenced_attrs() if not hasattr(BookPhotoEntry, a))
    assert not missing, f"BookPhotoEntry is missing referenced attribute(s): {missing}"


def test_uploaded_gaps_warning_formats_with_slots():
    text = BookPhotoEntry.uploaded_gaps_warning.format(slots="3, 4")
    assert "3, 4" in text
    assert "{slots}" not in text
