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
# Wave B (#104): book search is NOW a streamlit-keyup custom component (live,
# debounced) — it used to be a plain st.text_input. Type into it via fill_keyup.
BOOK_SEARCH_KEYUP = "book_search_keyup"
# Author search is also a streamlit-keyup custom component (live, debounced).
AUTHOR_SEARCH_KEYUP = "author_search_keyup"

# data_structures/book.py -> Book.to_form().  A brand-new (unsaved) book has an
# empty ``document_id`` (``title.lower().replace(" ", "_")`` of an empty title),
# so the per-entity suffix is empty and the key ends in a trailing underscore.
NEW_BOOK_TITLE = "book_form_title_"
NEW_BOOK_SUBMIT = "book_form_submit_"

# pages/review_my_books.py
REVIEW_BOOKS_SELECT = "review_books_select"
REVIEW_BOOKS_EDIT = "review_books_edit_button"

# pages/book_edit_home.py — the per-book management hub option menu and (#106)
# the Manage-characters button it routes into (rendered inside enter_text.py).
BOOK_EDIT_OPTION_MENU = "book_edit_option_menu"
ENTER_TEXT_MANAGE_CHARACTERS_BUTTON = "enter_text_manage_characters_button"

# pages/collection_picker.py (#75) — landing "View results" now routes HERE first.
COLLECTION_METHOD_MENU = "collection_method_menu"
COLLECTION_SEARCH_KEYUP = "collection_search_keyup"
# Single "View results" button (#163). It is always enabled: it scopes to the
# built selection, or to ALL books when nothing is selected. The separate
# "View all books" button was removed.
COLLECTION_VIEW_RESULTS_BUTTON = "collection_view_results_button"
# Left-hand quick add/remove dropdown + the "All books" predefined option (#163).
COLLECTION_SEARCH_MULTISELECT = "collection_search_multiselect"
COLLECTION_PREDEFINED_ALL_BOOKS_BUTTON = "collection_predefined_all_books_button"

# pages/validation.py (#47/#83) — gated to team/admin.
VALIDATION_SUBMITTED_TOGGLE = "validation_submitted_only_toggle"
VALIDATION_SELECT_BOOK = "validation_select_book"
VALIDATION_OPEN_REVIEW_BUTTON = "validation_open_review_button"

# pages/add_books_batch.py (#84) — reached via the "Batch Upload" user-home item.
# The file_uploader was replaced by the direct-to-S3 iframe uploader (#118), which
# has no widget key — assert on the "Detect books" button instead.
BATCH_DETECT_BUTTON = "add_books_batch_detect_button"

# --------------------------------------------------------------------------- #
# Visible text snippets (from the text_content module — assertion anchors).    #
# --------------------------------------------------------------------------- #

APP_TITLE = "Fair Tales Data Entry Tool"
INVALID_CREDENTIALS = "Invalid credentials."
LANDING_ENTER_DATA_LABEL = "Enter data"
LANDING_VIEW_RESULTS_LABEL = "View results"

# user_home option_menu items (UserHome.* / BookPhotoEntry.* / BatchBookEntry.*)
MENU_SEARCH_BOOKS = "Search Books"
MENU_SEARCH_AUTHORS = "Search Authors"
MENU_ADD_BOOK = "Add a Book"
MENU_ADD_FROM_PHOTOS = "Add from Photos"
MENU_BATCH_UPLOAD = "Batch Upload"
MENU_EDIT_BOOKS = "Edit my Books"

NO_MATCHING_BOOK = "No matching books found"
NO_MATCHING_AUTHOR = "No matching authors found"
TITLE_REQUIRED = "Book title is required."
RESULTS_PAGE_TITLE = "Research Results"
RESULTS_WIP_HEADER = "Work in progress"
NO_USER_BOOKS_HINT = "books"  # review_my_books warning when the user has none

# collection_picker.py (CollectionPicker.*)
COLLECTION_PAGE_TITLE = "Choose a book collection"
COLLECTION_MENU_SEARCH = "Search & select"
COLLECTION_MENU_PREDEFINED = "Predefined collections"
COLLECTION_MENU_PHOTO = "From photos"
COLLECTION_SEARCH_HEADER = "Search our database and tick the books you want"
COLLECTION_PREDEFINED_HEADER = "Browse predefined collections"
COLLECTION_PHOTO_HEADER = "Upload photos of your books"
# The file_uploader was replaced by the direct-to-S3 iframe uploader (#118), which
# carries no Streamlit widget key. Assert the photo method rendered via its
# always-present "Read books from photo(s)" button instead.
COLLECTION_PHOTO_EXTRACT_BUTTON = "collection_photo_extract_button"

# validation.py (Validation.*)
VALIDATION_LIST_HEADER = "Books to validate"
VALIDATION_NONE_PENDING = "There are no books awaiting validation right now."
VALIDATION_NOT_AUTHORISED = (
    "This page is only accessible to project team members and admins."
)

# add_books_batch.py (BatchBookEntry.*)
BATCH_HEADER = "Batch upload"  # full: "Batch upload — add several books at once"

# book_edit_home.py menu item + ManageCharacters.header (#106)
MENU_MANAGE_CHARACTERS = "Manage characters"
MANAGE_CHARACTERS_HEADER = "Manage characters and aliases"

# Sidebar nav link labels (utilities.page_layout). The role-gated links only
# render for the right tier (see role_gated_sidebar tests).
SIDEBAR_HOME_LINK = "Home"
SIDEBAR_SETTINGS_LINK = "Settings"
SIDEBAR_VALIDATION_LINK = "Data validation"
SIDEBAR_ADMIN_LINK = "Admin"

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


# --------------------------------------------------------------------------- #
# Sidebar helpers.                                                             #
# --------------------------------------------------------------------------- #
# The app sets ``initial_sidebar_state="collapsed"`` (utilities.page_layout), so
# the sidebar nav links are present in the DOM but hidden until the sidebar is
# opened. Presence assertions can read the collapsed DOM directly; clicking a
# link needs the sidebar expanded first.

SIDEBAR = "[data-testid='stSidebar']"


def open_sidebar(page) -> None:
    """Expand the collapsed sidebar if a collapsed-control button is present."""
    control = page.locator(
        "[data-testid='stSidebarCollapsedControl'] button, "
        "[data-testid='stExpandSidebarButton']"
    )
    if control.count() > 0 and control.first.is_visible():
        control.first.click()
        page.wait_for_timeout(400)


def sidebar_link(page, label: str):
    """Return a locator for a sidebar ``st.page_link`` by its visible label.

    Scoped to the sidebar container and matched on the exact link text. Works
    whether or not the sidebar is expanded (the element stays in the DOM), so it
    is safe to call ``.count()`` on the result for a presence check.
    """
    return page.locator(SIDEBAR).get_by_role("link", name=label, exact=True)
