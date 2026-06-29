"""Manage-characters-for-a-book journey (read-only, best-effort).

The per-book management hub is ``book_edit_home.py`` (reached via user-home ->
'Edit my Books' -> select a book -> Edit). Its option menu now has FIVE items
(Instructions / Edit metadata / Upload photos / Enter text / Manage characters),
the last one a dedicated route added in #106 that opens the character-management
view inside ``enter_text.py``.

The deepest 'Manage characters' view only renders for a book whose page photos
are uploaded (``manage_characters`` warns 'please upload photos' otherwise). To
stay deterministic and read-only these tests:

* reach the per-book hub and assert its option menu renders, and
* (best-effort) click the 'Manage characters' menu item and assert the route
  lands in one of its two known states — the ManageCharacters header (photos
  uploaded) OR the 'please upload photos' warning (photos not uploaded).

Both tests skip cleanly when the test user has no in-progress books. Seed a book
(ideally one with photos uploaded) to exercise the full deep view.
"""

import pytest

import helpers as h


def _open_edit_my_books(page):
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    h.click_option_menu(page, h.MENU_EDIT_BOOKS, wrapper_key=h.USER_OPTION_MENU)


def _open_book_edit_hub(page):
    """Reach book_edit_home for the first in-progress book, or skip if none."""
    _open_edit_my_books(page)
    # review_my_books renders either a book selector (user has in-progress books)
    # or a 'no books' warning. Wait briefly for whichever resolves.
    page.wait_for_timeout(1500)
    select = page.locator(h.key(h.REVIEW_BOOKS_SELECT))
    if select.count() == 0:
        pytest.skip(
            "Test user has no in-progress books; seed one to exercise the "
            "per-book management hub and Manage characters view."
        )
    edit_button = page.locator(f"{h.key(h.REVIEW_BOOKS_EDIT)} button")
    assert edit_button.is_visible()
    edit_button.click()
    page.locator(h.key(h.BOOK_EDIT_OPTION_MENU)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_book_management_hub_loads(logged_in_page):
    """Reach 'Edit my Books'; if the user has books, the edit hub is reachable."""
    page = logged_in_page
    _open_book_edit_hub(page)
    assert page.locator(h.key(h.BOOK_EDIT_OPTION_MENU)).is_visible()


def test_manage_characters_route_reaches_known_state(logged_in_page):
    """#106: the 'Manage characters' menu item routes to a known UI state.

    Clicking it opens enter_text.py's manage view when photos are uploaded, or
    surfaces the 'please upload photos' warning when they are not. Either is a
    valid, deterministic landing state — we assert one of them appears.
    """
    page = logged_in_page
    _open_book_edit_hub(page)

    # The 'Manage characters' option menu item (book_edit_home option_menu).
    h.click_option_menu(
        page, h.MENU_MANAGE_CHARACTERS, wrapper_key=h.BOOK_EDIT_OPTION_MENU
    )
    page.wait_for_timeout(1500)

    manage_header = page.get_by_text(h.MANAGE_CHARACTERS_HEADER, exact=False)
    # Alerts.please_uploaded_photos: "Please upload photos of the book pages first!"
    upload_warning = page.get_by_text("upload photos", exact=False)
    assert manage_header.count() > 0 or upload_warning.count() > 0
