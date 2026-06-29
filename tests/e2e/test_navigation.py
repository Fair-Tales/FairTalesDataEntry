"""Navigation journeys: landing choices and the user-home option menu."""

import helpers as h


def test_landing_enter_data_opens_user_home(logged_in_page):
    """Landing -> 'Enter data' reaches the user-home menu (search box visible)."""
    page = logged_in_page
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    # user_home's default option is 'Search Books' -> the book-search keyup mounts.
    page.locator(h.key(h.BOOK_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_landing_view_results_opens_collection_picker(logged_in_page):
    """Landing -> 'View results' now routes to the collection picker (#75)."""
    page = logged_in_page
    page.locator(f"{h.key(h.LANDING_VIEW_RESULTS)} button").click()
    # The picker page title + its method option_menu mount.
    page.get_by_text(h.COLLECTION_PAGE_TITLE, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    page.locator(h.key(h.COLLECTION_METHOD_MENU)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_user_home_option_menu_switches_to_author_search(logged_in_page):
    """The user-home option_menu switches Search Books -> Search Authors."""
    page = logged_in_page
    page.locator(f"{h.key(h.LANDING_ENTER_DATA)} button").click()
    page.locator(h.key(h.BOOK_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    h.click_option_menu(page, h.MENU_SEARCH_AUTHORS, wrapper_key=h.USER_OPTION_MENU)
    # The author-search keyup widget replaces the book-search one.
    page.locator(h.key(h.AUTHOR_SEARCH_KEYUP)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
