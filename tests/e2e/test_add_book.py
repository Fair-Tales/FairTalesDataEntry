"""Manual 'Add a Book' journey: the form renders and required-field validation.

Read-only by design: we never submit a *valid* book, so no data is created. We
only assert the empty form renders and that submitting a blank title surfaces the
'Book title is required.' warning (the validation path returns before any write).
"""

import helpers as h


def _open_add_book_form(page):
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_INPUT)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    # 'Add a Book' navigates to add_book.py, which renders Book.to_form().
    h.click_option_menu(page, h.MENU_ADD_BOOK, wrapper_key=h.USER_OPTION_MENU)
    page.locator(f"{h.key(h.NEW_BOOK_TITLE)} input").wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_add_book_form_renders(logged_in_page):
    """The Add a Book form mounts with a (blank) title input and a submit button."""
    page = logged_in_page
    _open_add_book_form(page)
    assert page.locator(f"{h.key(h.NEW_BOOK_TITLE)} input").is_visible()
    assert page.locator(f"{h.key(h.NEW_BOOK_SUBMIT)} button").is_visible()


def test_add_book_blank_title_validation(logged_in_page):
    """Submitting the form with an empty title shows the required-field warning."""
    page = logged_in_page
    _open_add_book_form(page)
    # Leave the title blank and submit.
    page.locator(f"{h.key(h.NEW_BOOK_SUBMIT)} button").click()
    page.get_by_text(h.TITLE_REQUIRED, exact=False).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
