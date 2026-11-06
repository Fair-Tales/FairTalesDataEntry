"""Photo-upload journey (#114 — photo_upload.py, pages/add_book_photos.py).

This is the entry flow the pilot actually uses, and it was the one path with no
automated coverage: its ``<input type="file">`` is ``display:none`` inside a
component iframe, so it cannot be clicked and the accessibility tree does not
expose it. Playwright reaches it anyway — ``set_input_files`` does not require
visibility and the frame is same-origin — which is what these tests exercise.

Scope is deliberately the upload half only. We never click **Go**, so no book is
created, no Claude vision call is billed, and the photos land in the disposable
``uploads/{flow_key}/{session_id}/`` prefix the app already treats as scratch.
"""

import pytest
from PIL import Image

import helpers as h


@pytest.fixture
def page_photos(tmp_path):
    """Two small generated JPEGs, so no binary fixtures live in the repo.

    Content does not matter: the uploader PUTs bytes and does not inspect them,
    and we stop before anything reads the images.
    """
    paths = []
    for index, colour in enumerate(((190, 120, 90), (90, 140, 190)), start=1):
        path = tmp_path / f"page_{index:02d}.jpg"
        Image.new("RGB", (800, 1000), colour).save(path, "JPEG", quality=70)
        paths.append(path)
    return paths


def _open_photo_entry(page):
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    h.require_menu_item(page, h.MENU_ADD_FROM_PHOTOS, h.USER_OPTION_MENU)
    h.click_option_menu(page, h.MENU_ADD_FROM_PHOTOS, wrapper_key=h.USER_OPTION_MENU)


def test_photo_uploader_mounts(logged_in_page):
    """The uploader component mounts and exposes its file input."""
    page = logged_in_page
    _open_photo_entry(page)
    frame = h.photo_uploader_frame(page)
    assert frame.query_selector(h.PHOTO_UPLOAD_INPUT) is not None
    # The visible control is a styled label; the input itself stays hidden.
    assert frame.query_selector(h.PHOTO_UPLOAD_SUMMARY) is not None


def test_photo_upload_reports_every_file_uploaded(logged_in_page, page_photos):
    """Selecting photos PUTs each one and the summary reports them all done.

    This is the real presigned-PUT path against S3, not a stub: a regression in
    URL minting, CORS, or the component's change handler shows up here as a
    summary that never reaches "2 of 2".
    """
    page = logged_in_page
    _open_photo_entry(page)

    summary = h.upload_photos(page, page_photos)

    assert f"{len(page_photos)} of {len(page_photos)}" in summary
    # Per-file rows are rendered; none should carry the failure marker.
    frame = h.photo_uploader_frame(page)
    assert "✗" not in (frame.text_content(h.PHOTO_UPLOAD_LIST) or "")


def test_read_button_present_after_upload(logged_in_page, page_photos):
    """'Go' is offered once photos have uploaded — but is never clicked here.

    Clicking it fetches the photos and calls Claude vision to pre-fill the book,
    which creates data and costs money, so extraction stays out of the suite.
    """
    page = logged_in_page
    _open_photo_entry(page)
    h.upload_photos(page, page_photos)

    read_button = page.locator(h.key(h.ADD_BOOK_PHOTOS_READ_BUTTON)).get_by_role(
        "button", name=h.ADD_BOOK_PHOTOS_READ_LABEL, exact=True
    )
    read_button.wait_for(state="visible", timeout=h.RERUN_TIMEOUT)
    assert read_button.is_enabled()
