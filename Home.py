import streamlit as st
from utilities import (
    is_authenticated,
    FirestoreWrapper,
    load_author_dict,
    load_publisher_dict,
    load_illustrator_dict,
    load_book_dict,
    load_character_dict,
)
from data_structures import Author, Book, Illustrator, Publisher
from cookie_auth import init_cookie_manager, restore_session_from_cookie


def initialise():
    st.session_state['firestore'] = FirestoreWrapper(auth=True)
    st.session_state['current_book'] = Book()
    st.session_state['author'] = Author()
    st.session_state['publisher'] = Publisher()
    st.session_state['illustrator'] = Illustrator()
    st.session_state['active_form_to_confirm'] = None

    # Lookup dicts are served from cached resource loaders (see utilities.py)
    # rather than re-streaming every collection on each session init (issue #53).
    # We shallow-copy each cached dict into session_state so that in-session
    # mutations (a freshly registered author/book/etc. added by the
    # FormConfirmation.confirm_new_* methods or Character.register) only affect
    # this session and never poison the shared cache. Those writes also call the
    # matching ``load_*_dict.clear()`` so subsequent sessions re-read from
    # Firestore — preserving write-through freshness.
    st.session_state['author_dict'] = dict(load_author_dict())
    st.session_state['publisher_dict'] = dict(load_publisher_dict())
    st.session_state['illustrator_dict'] = dict(load_illustrator_dict())
    st.session_state['book_dict'] = dict(load_book_dict())
    st.session_state['character_dict'] = dict(load_character_dict())

def ensure_session():
    """Idempotently initialise the per-session Firestore client and lookup dicts.

    Runs the full ``initialise()`` at most once per session, keyed on the
    presence of ``st.session_state['firestore']``. This is the safe entry point
    to call *before* any page body executes — both from ``Home.py`` ahead of
    ``navigate_pages()`` and from the public logged-out deep-link pages — so a
    hard refresh of a deep page (e.g. ``/enter_text``) always finds ``firestore``
    and the lookup dicts already populated before the page runs (#107).

    Crucially it does NOT set the ``initialised`` flag and does NOT redirect to
    login, so calling it never disturbs the first-load ``/`` -> login routing nor
    the public deep-link flows (confirm / reset_password / qr_landing).
    """
    if 'firestore' not in st.session_state:
        initialise()

def navigate_pages():
    
    pages = {
        "Menu":[
            st.Page("./pages/login.py", title='Sign Out'),
            st.Page("./pages/account_settings.py", title='Account Settings'),
            st.Page("./pages/landing.py", title='Home'),
            st.Page("./pages/user_home.py", title='Enter Data'),
            st.Page("./pages/priority_books.py", title='Books We Need'),
            st.Page("./pages/report_feedback.py", title='Report a Bug / Feature'),
        ],
        "Other pages":[
            st.Page("./pages/add_author.py"),
            st.Page("./pages/add_illustrator.py"),
            st.Page("./pages/add_publisher.py"),
            st.Page("./pages/add_book.py"),
            st.Page("./pages/add_book_photos.py"),
            st.Page("./pages/add_character.py"),
            st.Page("./pages/book_data_entry.py"),
            st.Page("./pages/book_edit_home.py"),
            st.Page("./pages/confirm_entry.py"),
            st.Page("./pages/confirm.py"),
            st.Page("./pages/reset_password.py"),
            st.Page("./pages/enter_text.py"),
            st.Page("./pages/page_photo_upload.py"),
            st.Page("./pages/qr_landing.py"),
            st.Page("./pages/register_user_done.py"),
            st.Page("./pages/register_user.py"),
            st.Page("./pages/review_my_books.py"),
            st.Page("./pages/uploader.py"),
            st.Page("./pages/donate.py"),
            st.Page("./pages/collection_picker.py"),
            st.Page("./pages/results_dashboard.py"),
            st.Page("./pages/add_books_batch.py"),
            # Reconstruct orphaned books is no longer a sidebar item (#141); it is
            # reached from a gated link at the bottom of the Admin page. It must
            # stay registered here so that st.page_link()/st.switch_page() can
            # navigate to it — its own team-and-above gating lives on the page.
            st.Page("./pages/reconstruct_orphans.py"),
        ]
    }

    # Role-based extra pages (#83/#47). Team members and admins can reach the
    # data-validation page directly from the sidebar; admin-only tools (the Admin
    # page) stay admin-gated and remain hidden from team members.
    role = st.session_state.get('role', 'archivist')
    is_admin_user = st.session_state.get('admin', False) or role == 'admin'
    is_team_or_above = is_admin_user or role == 'team'

    if is_team_or_above:
        pages["Menu"].append(st.Page("./pages/validation.py", title='Data validation'))
    if is_admin_user:
        pages["Menu"].append(st.Page("./pages/admin.py", title='Admin'))

    st.navigation(pages, position="hidden").run()

if __name__ == "__main__":

    # Guarantee firestore + lookup dicts exist BEFORE the current page runs, so a
    # hard refresh of a deep page does not hit 'firestore' missing (#107). This is
    # idempotent and has no redirect side-effect.
    ensure_session()

    # Persistent "Remember me" login (#111). init_cookie_manager() builds the
    # cookie component once per run (required before any page sets/reads cookies);
    # restore_session_from_cookie() re-establishes an authenticated session from a
    # valid signed cookie BEFORE any page body runs — re-resolving role/admin from
    # the DB, never trusting the cookie. Both are no-ops when no cookie_signing_key
    # secret is configured, so deployments without it behave exactly as before.
    init_cookie_manager()
    restore_session_from_cookie()

    navigate_pages()

    # First-load routing to login. 'initialised' (distinct from 'firestore' above)
    # still governs this redirect; the public deep-link pages set it themselves to
    # opt out, so reordering the init above does not change their behaviour.
    # Skip the redirect when a remember-me cookie has just re-authenticated the
    # user (#111), so a valid persistent session lands on their page, not login.
    if 'initialised' not in st.session_state:
        st.session_state['initialised'] = True
        if not is_authenticated():
            st.session_state['admin'] = False
            st.switch_page("./pages/login.py")