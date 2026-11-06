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


def test_landing_view_results_opens_dashboard(logged_in_page):
    """Landing -> 'View results' routes straight to the dashboard (#163).

    It seeds an empty selection, which scopes the dashboard to ALL books. The
    collection picker is no longer the landing target; reaching it from the
    dashboard is covered in test_collection_picker.py.
    """
    page = logged_in_page
    page.locator(f"{h.key(h.LANDING_VIEW_RESULTS)} button").click()
    page.get_by_text(h.RESULTS_PAGE_TITLE, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    # The dashboard offers the route onward to the picker.
    page.locator(f"{h.key(h.RESULTS_DASHBOARD_CHANGE_SELECTION)} button").wait_for(
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
