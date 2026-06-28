"""Manage-characters-for-a-book journey (read-only, best-effort).

The per-book management hub is ``book_edit_home.py`` (reached via user-home ->
'Edit my Books' -> select a book -> Edit). Its option menu (Instructions / Edit
metadata / Upload photos / Enter text) is the entry point to a book's data,
including the deep 'Manage characters' view inside ``enter_text.py``.

That deepest view only appears for a book whose page photos are uploaded, which
needs a prepared fixture book. To stay deterministic and read-only this test
reaches the per-book hub and asserts it renders; it skips cleanly when the test
user has no in-progress books.

EXTENSION POINT: given a fixtured book (page photos uploaded), drive
book_edit_home's option_menu to 'Enter text', then click the
``enter_text_manage_characters_button`` and assert the
ManageCharacters.header ("Manage characters and aliases") renders.
"""

import pytest

import helpers as h


def _open_edit_my_books(page):
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_INPUT)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    h.click_option_menu(page, h.MENU_EDIT_BOOKS, wrapper_key=h.USER_OPTION_MENU)


def test_book_management_hub_loads(logged_in_page):
    """Reach 'Edit my Books'; if the user has books, the edit hub is reachable."""
    page = logged_in_page
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

    # book_edit_home renders the per-book option menu (its st-key wrapper holds
    # the component iframe).
    page.locator(h.key(h.BOOK_EDIT_OPTION_MENU)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
