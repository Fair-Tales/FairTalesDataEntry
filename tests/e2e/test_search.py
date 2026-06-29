"""Search journeys: book and author live search.

These are deterministic regardless of DB contents: we type a deliberately
non-matching query and assert the 'no matching ...' warning. (A separate, looser
assertion path is left as an extension point for matching queries once a known
fixture book/author exists in the dev DB.)
"""

import helpers as h

# A query string highly unlikely to match any real book or author title.
NONSENSE_QUERY = "zzqxnomatch12345"


def _open_user_home(page):
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_book_search_no_match_warning(logged_in_page):
    """Typing a nonsense book title shows the 'no matching books' warning."""
    page = logged_in_page
    _open_user_home(page)
    # Wave B (#104): book search is now a live st_keyup component, like authors.
    h.fill_keyup(page, h.BOOK_SEARCH_KEYUP, NONSENSE_QUERY)
    page.get_by_text(h.NO_MATCHING_BOOK, exact=False).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_author_search_no_match_warning(logged_in_page):
    """Typing a nonsense author name shows the 'no matching authors' warning."""
    page = logged_in_page
    _open_user_home(page)
    h.click_option_menu(page, h.MENU_SEARCH_AUTHORS, wrapper_key=h.USER_OPTION_MENU)
    page.locator(h.key(h.AUTHOR_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    h.fill_keyup(page, h.AUTHOR_SEARCH_KEYUP, NONSENSE_QUERY)
    page.get_by_text(h.NO_MATCHING_AUTHOR, exact=False).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
