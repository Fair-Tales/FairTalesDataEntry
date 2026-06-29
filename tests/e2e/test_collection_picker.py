"""Collection-picker journeys (#75 — pages/collection_picker.py).

Deterministic, non-AI coverage. The landing page's 'View results' now routes
here first. We assert:

* the page renders (title, method option_menu, the two view-results buttons),
* each of the three method tabs renders its own stable widget,
* the search-and-select method shows a 'no matching books' warning for a
  nonsense query (mirrors user_home's book search), and
* the 'View results for this collection' button is disabled while the selection
  is empty (it enables only once a book is ticked).

The 'From photos' method calls Claude vision on submit; per DECISIONS.md 004 we
do NOT trigger it — we only assert the upload widget renders.
"""

import helpers as h

NONSENSE_QUERY = "zzqxnomatch12345"


def _open_picker(page):
    page.locator(f"{h.key(h.LANDING_VIEW_RESULTS)} button").click()
    page.locator(h.key(h.COLLECTION_METHOD_MENU)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_collection_picker_renders(logged_in_page):
    """Title, method menu, and both view-results buttons mount."""
    page = logged_in_page
    _open_picker(page)
    assert page.get_by_text(h.COLLECTION_PAGE_TITLE, exact=False).first.is_visible()
    assert page.locator(f"{h.key(h.COLLECTION_VIEW_ALL_BUTTON)} button").is_visible()
    assert page.locator(h.key(h.COLLECTION_VIEW_RESULTS_BUTTON)).count() > 0


def test_collection_method_tabs_render(logged_in_page):
    """Each of the three method tabs renders its own stable widget."""
    page = logged_in_page
    _open_picker(page)

    # Method 1: Search & select -> live search keyup + its header.
    h.click_option_menu(
        page, h.COLLECTION_MENU_SEARCH, wrapper_key=h.COLLECTION_METHOD_MENU
    )
    page.locator(h.key(h.COLLECTION_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )

    # Method 2: Predefined collections -> its browse header.
    h.click_option_menu(
        page, h.COLLECTION_MENU_PREDEFINED, wrapper_key=h.COLLECTION_METHOD_MENU
    )
    page.get_by_text(h.COLLECTION_PREDEFINED_HEADER, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )

    # Method 3: From photos -> the upload widget (we never trigger extraction).
    h.click_option_menu(
        page, h.COLLECTION_MENU_PHOTO, wrapper_key=h.COLLECTION_METHOD_MENU
    )
    page.locator(h.key(h.COLLECTION_PHOTO_UPLOADER)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_collection_search_no_match_warning(logged_in_page):
    """Search method: a nonsense query shows the 'no matching books' warning."""
    page = logged_in_page
    _open_picker(page)
    h.click_option_menu(
        page, h.COLLECTION_MENU_SEARCH, wrapper_key=h.COLLECTION_METHOD_MENU
    )
    h.fill_keyup(page, h.COLLECTION_SEARCH_KEYUP, NONSENSE_QUERY)
    page.get_by_text(h.NO_MATCHING_BOOK, exact=False).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_view_results_disabled_when_selection_empty(logged_in_page):
    """With nothing selected, 'View results for this collection' is disabled."""
    page = logged_in_page
    _open_picker(page)
    button = page.locator(f"{h.key(h.COLLECTION_VIEW_RESULTS_BUTTON)} button")
    button.wait_for(state="attached", timeout=h.RERUN_TIMEOUT)
    assert button.is_disabled()
