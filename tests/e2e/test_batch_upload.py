"""Batch-upload entry journey (#84 — pages/add_books_batch.py).

Deterministic, non-AI. The 'Batch Upload' user-home menu item opens the batch
page at its upload step. We assert the page header and the file-upload widget +
'Detect books' button render. We never upload photos or click 'Detect books'
(that step splits the batch and calls Claude vision — out of scope per
DECISIONS.md 004).
"""

import helpers as h


def _open_batch(page):
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    # 'Batch Upload' (BatchBookEntry.menu_label) -> add_books_batch.py.
    h.click_option_menu(page, h.MENU_BATCH_UPLOAD, wrapper_key=h.USER_OPTION_MENU)


def test_batch_upload_page_renders(logged_in_page):
    """The batch page opens on its upload step with header + upload widget."""
    page = logged_in_page
    _open_batch(page)
    page.get_by_text(h.BATCH_HEADER, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    assert page.locator(h.key(h.BATCH_UPLOADER)).is_visible()


def test_batch_detect_button_present(logged_in_page):
    """The 'Detect books' button renders on the upload step (we never click it)."""
    page = logged_in_page
    _open_batch(page)
    page.locator(f"{h.key(h.BATCH_DETECT_BUTTON)} button").wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
