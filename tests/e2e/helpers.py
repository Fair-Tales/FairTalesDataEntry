"""Shared selectors and interaction helpers for the Playwright e2e suite (#82).

Streamlit renders a widget given ``key="foo"`` inside a wrapper element carrying
the CSS class ``st-key-foo`` (Streamlit >= 1.39; this project runs 1.58). The
project added stable, predictable keys to its widgets in #80 using the scheme
``<context>_<purpose>_<type>`` (and ``<entity>_form_<field>_<document_id>`` for
entity forms). These helpers turn those keys into reliable selectors.

Two project widgets are *custom Streamlit components* rendered inside an
``<iframe>`` rather than plain HTML, so they need ``frame_locator`` to reach the
real element:

* ``streamlit-option-menu`` (the horizontal nav menus) — clicked via
  :func:`click_option_menu`.
* ``streamlit-keyup`` / ``st_keyup`` (the live search boxes) — typed into via
  :func:`fill_keyup`.

The ``st-key-<key>`` wrapper class is still applied to the element container of a
custom component, so we anchor on that wrapper and then descend into its iframe.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Widget keys (read directly from the page .py files — keep in sync with #80). #
# --------------------------------------------------------------------------- #

# pages/login.py
LOGIN_EMAIL = "login_email"
LOGIN_PASSWORD = "login_password"
LOGIN_SUBMIT = "login_submit_button"
LOGIN_SIGN_OUT = "login_sign_out_button"

# pages/landing.py
LANDING_ENTER_DATA = "landing_enter_data_button"
LANDING_VIEW_RESULTS = "landing_view_results_button"

# pages/user_home.py
USER_OPTION_MENU = "user_option_menu"
# Book search is a plain st.text_input (rerun on Enter / blur).
BOOK_SEARCH_INPUT = "user_home_book_search_input"
# Author search is a streamlit-keyup custom component (live, debounced).
AUTHOR_SEARCH_KEYUP = "author_search_keyup"

# data_structures/book.py -> Book.to_form().  A brand-new (unsaved) book has an
# empty ``document_id`` (``title.lower().replace(" ", "_")`` of an empty title),
# so the per-entity suffix is empty and the key ends in a trailing underscore.
NEW_BOOK_TITLE = "book_form_title_"
NEW_BOOK_SUBMIT = "book_form_submit_"

# pages/review_my_books.py
REVIEW_BOOKS_SELECT = "review_books_select"
REVIEW_BOOKS_EDIT = "review_books_edit_button"

# pages/book_edit_home.py
BOOK_EDIT_OPTION_MENU = "book_edit_option_menu"

# --------------------------------------------------------------------------- #
# Visible text snippets (from the text_content module — assertion anchors).    #
# --------------------------------------------------------------------------- #

APP_TITLE = "Fair Tales Data Entry Tool"
INVALID_CREDENTIALS = "Invalid credentials."
LANDING_ENTER_DATA_LABEL = "Enter data"
LANDING_VIEW_RESULTS_LABEL = "View results"
MENU_SEARCH_BOOKS = "Search Books"
MENU_SEARCH_AUTHORS = "Search Authors"
MENU_ADD_BOOK = "Add a Book"
MENU_EDIT_BOOKS = "Edit my Books"
NO_MATCHING_BOOK = "No matching books found"
NO_MATCHING_AUTHOR = "No matching authors found"
TITLE_REQUIRED = "Book title is required."
RESULTS_PAGE_TITLE = "Research Results"
RESULTS_WIP_HEADER = "Work in progress"
NO_USER_BOOKS_HINT = "books"  # review_my_books warning when the user has none

# Default wait for a Streamlit rerun to settle (ms). Streamlit reruns are fast
# but the websocket round-trip plus component (iframe) mounts need a beat.
RERUN_TIMEOUT = 15000


def key(name: str) -> str:
    """Return the CSS selector for a Streamlit widget's ``st-key-`` wrapper."""
    return f".st-key-{name}"


# --------------------------------------------------------------------------- #
# Custom-component interaction helpers.                                        #
# --------------------------------------------------------------------------- #

def fill_text_input(page, wrapper_key: str, text: str) -> None:
    """Type ``text`` into a plain ``st.text_input`` and commit it.

    A plain text_input only reruns Streamlit on Enter / blur, so we fill the
    field and press Enter, then wait for the rerun to settle.
    """
    field = page.locator(key(wrapper_key)).locator("input")
    field.wait_for(state="visible", timeout=RERUN_TIMEOUT)
    field.fill(text)
    field.press("Enter")
    page.wait_for_timeout(800)


def fill_keyup(page, wrapper_key: str, text: str) -> None:
    """Type ``text`` into a ``st_keyup`` live-search box.

    ``st_keyup`` is a custom component, so the real ``<input>`` lives inside the
    component iframe nested under the ``st-key-<wrapper_key>`` wrapper. We type
    character-by-character so the component's keyup listener (and its 300ms
    debounce) fires the rerun, then wait for the debounce to elapse.
    """
    wrapper = page.locator(key(wrapper_key))
    wrapper.wait_for(state="visible", timeout=RERUN_TIMEOUT)
    field = wrapper.frame_locator("iframe").locator("input")
    field.click()
    field.fill("")
    field.press_sequentially(text, delay=40)
    # Let the 300ms debounce expire and the Streamlit rerun settle.
    page.wait_for_timeout(1200)


def click_option_menu(page, label: str, wrapper_key: str | None = None) -> None:
    """Click an item in a ``streamlit-option-menu`` horizontal nav menu.

    The menu is a custom component rendered in an iframe. When ``wrapper_key`` is
    given we anchor on that widget's ``st-key-`` wrapper (robust when several
    option menus could exist); otherwise we target the option-menu component
    iframe by its title.
    """
    if wrapper_key is not None:
        frame = page.locator(key(wrapper_key)).frame_locator("iframe")
    else:
        frame = page.frame_locator(
            "iframe[title='streamlit_option_menu.option_menu']"
        )
    item = frame.get_by_text(label, exact=True)
    item.wait_for(state="visible", timeout=RERUN_TIMEOUT)
    item.click()
    page.wait_for_timeout(800)
