import streamlit as st
from streamlit_option_menu import option_menu
from st_keyup import st_keyup
from text_content import Alerts, Instructions, old_books, BookPhotoEntry, BatchBookEntry, UserHome
from utilities import check_authentication_status, page_layout, navigate_to, clear_page_history, clear_entity_form_state
from data_structures import Book
from photo_upload import reset_upload_session
import pandas as pd

check_authentication_status()


def _person_name_from_ref(ref):
    """Resolve an author/illustrator value to a display name, tolerant of legacy
    data: a Firestore DocumentReference (-> single ``name`` field, or the legacy
    'forename surname' pair), a plain string stored instead of a reference (used
    directly), a deleted/empty doc, or a missing value (-> the Unknown label).

    Illustrators are now stored as a single ``name`` field (#156); authors and
    legacy illustrator records still use forename/surname, so prefer ``name`` when
    present and fall back to joining forename/surname."""
    if ref is None:
        return UserHome.unknown
    if isinstance(ref, str):
        return ref.replace('_', ' ').strip() or UserHome.unknown
    if hasattr(ref, 'get'):
        data = ref.get().to_dict() or {}
        name = (data.get('name') or '').strip()
        if name:
            return name
        return ' '.join(
            [data.get('forename', ''), data.get('surname', '')]
        ).strip() or UserHome.unknown
    return UserHome.unknown


def _publisher_name_from_ref(ref):
    """Resolve a publisher value to a display name, tolerant of legacy data
    (DocumentReference, plain string, deleted/empty doc, or missing)."""
    if ref is None:
        return UserHome.unknown
    if isinstance(ref, str):
        return ref.replace('_', ' ').strip() or UserHome.unknown
    if hasattr(ref, 'get'):
        data = ref.get().to_dict() or {}
        return data.get('name', UserHome.unknown)
    return UserHome.unknown


# TODO: migrate to a proper search service like Algolia?
def book_search():
    # Live-filter as the user types (issue #104). st_keyup reruns on each keystroke;
    # the 300ms debounce limits how often the book lookup runs while typing.
    book_search_string = st_keyup(
        UserHome.book_search_label,
        value="",
        debounce=300,
        key="book_search_keyup",
    )
    if book_search_string and len(book_search_string) > 0:
        search_term = book_search_string.lower()

        matching_titles = [
            title for title in st.session_state['book_dict'].keys()
            if search_term in title.lower()
        ]

        if len(matching_titles) == 0:
            st.warning(Alerts.no_matching_book)
        else:
            st.write(UserHome.results_found.format(count=len(matching_titles)))
            for title in matching_titles:
                book_ref = st.session_state['book_dict'][title]
                book_data = book_ref.get().to_dict()

                author_name = _person_name_from_ref(book_data.get('author'))

                published = book_data.get('published', '')
                year_str = f" ({published})" if published else ""

                with st.expander(UserHome.book_expander.format(title=title, year_str=year_str, author=author_name)):
                    publisher_name = _publisher_name_from_ref(book_data.get('publisher'))
                    illustrator_name = _person_name_from_ref(book_data.get('illustrator'))

                    st.write(UserHome.publisher_label.format(name=publisher_name))
                    st.write(UserHome.illustrator_label.format(name=illustrator_name))

def author_search():
    # Live-filter as the user types (issue #72). st_keyup reruns on each keystroke;
    # the 300ms debounce limits how often the author lookup runs while typing.
    author_search_string = st_keyup(
        Instructions.author_search_label,
        value="",
        debounce=300,
        key="author_search_keyup",
    )
    if author_search_string and len(author_search_string) > 0:
        search_term = author_search_string.lower()

        matching_names = [
            full_name for full_name in st.session_state['author_dict'].keys()
            if search_term in full_name.lower()
        ]

        if len(matching_names) == 0:
            st.warning(Alerts.no_matching_author)
        else:
            st.write(UserHome.results_found.format(count=len(matching_names)))
            for full_name in matching_names:
                author_ref = st.session_state['author_dict'][full_name]
                author_data = author_ref.get().to_dict()

                # Author date of birth was dropped (#149); the expander now shows
                # name + gender only.
                gender = author_data.get('gender') or UserHome.not_recorded

                with st.expander(UserHome.author_expander.format(name=full_name, gender=gender)):
                    books_df = st.session_state.firestore.get_by_field(
                        collection='books',
                        field='author',
                        match=author_ref
                    )
                    if books_df.empty:
                        st.write(UserHome.no_books_for_author)
                    else:
                        st.write(UserHome.books_label)
                        for _, book_row in books_df.iterrows():
                            title = book_row.get('title', UserHome.unknown_title)
                            published = book_row.get('published', '')
                            year_str = f" ({published})" if published else ""
                            st.write(f"- {title}{year_str}")


def add_book():
    st.session_state['current_book'] = Book()
    # Clear any leftover entity selections / flow flag from a previous (cancelled)
    # book entry so the new book form starts blank.
    for _key in ('current_author', 'current_illustrator', 'current_publisher', 'adding_book_entries'):
        st.session_state.pop(_key, None)
    # Drop persisted book-form widget state so the new (empty document_id) book
    # re-seeds from value=/index= rather than inheriting the previous new book's
    # values (see #80).
    clear_entity_form_state("book_form_")
    navigate_to("./pages/add_book.py")

def add_book_from_photos():
    st.session_state['current_book'] = Book()
    # Clear any leftover entity selections / flow flags / extraction state from a
    # previous (cancelled) book entry so the photo-first flow starts blank.
    for _key in (
        'current_author', 'current_illustrator', 'current_publisher',
        'adding_book_entries', 'extracted_author_name',
        'extracted_illustrator_name', 'extracted_publisher_name',
        'photo_first_pages', 'book_extraction', 'book_extraction_raw',
        '_upload_pipeline_done',
        # Photo-first AI pre-fill captions (#155/#150) and the auto-run poll state
        # so a fresh photo entry doesn't inherit the previous book's flags.
        'ai_prefilled_author', 'ai_prefilled_illustrator',
        'ai_prefilled_publisher', 'ai_prefilled_year',
        'photos_ready_auto', 'photo_extract_empty', 'photo_extract_diag',
        '_auto_last_count', '_auto_polls',
    ):
        st.session_state.pop(_key, None)
    # Start a fresh direct-to-S3 upload session (#114) so the new entry mints its
    # own uploads/single/{session_id}/ prefix rather than reusing the previous one.
    reset_upload_session("single")
    # Drop persisted book-form widget state so the new (empty document_id) book
    # re-seeds from value=/index= rather than inheriting the previous new book's
    # values (see #80).
    clear_entity_form_state("book_form_")
    navigate_to("./pages/add_book_photos.py")

def add_books_batch():
    # Clear any leftover batch-flow state so a new batch starts at the upload step.
    for _key in ('batch_step', 'batch_method', 'batch_detected', 'batch_results'):
        st.session_state.pop(_key, None)
    # Mint a fresh direct-to-S3 upload session (#118) for the batch's temp prefix
    # (uploads/batch/{session_id}/) so a new batch never reuses the previous one.
    reset_upload_session("batch")
    navigate_to("./pages/add_books_batch.py")

def review_my_books():
    navigate_to("./pages/review_my_books.py")

clear_page_history()
page_layout(current_page="./pages/user_home.py")

st.title(Instructions.app_title)

st.write(Instructions.home_intro)
st.write(Instructions.advise_to_search)

selected_option = option_menu(
    None,
    [UserHome.menu_search_books, UserHome.menu_search_authors, UserHome.menu_add_book, BookPhotoEntry.menu_label, BatchBookEntry.menu_label, UserHome.menu_edit_books],
    default_index=0,
    icons=['search', 'search', 'database-add', 'camera', 'images', 'pencil-square'],
    menu_icon="cast", orientation="horizontal",
    key="user_option_menu",
    styles={
        # The menu now has 6 items and wraps to a second row on narrow phone
        # screens. Vertical margin gives the wrapped row breathing room so its
        # icon isn't clipped by the row above; the smaller font reduces wrapping.
        "container": {"flex-wrap": "wrap", "padding": "0.25rem 0"},
        "nav-link": {"font-size": "13px", "text-align": "center", "margin": "4px 2px", "--hover-color": "#eee"},
        "nav-link-selected": {"background-color": "green"},
    }
)

navigation_dict = {
    UserHome.menu_search_books: book_search,
    UserHome.menu_search_authors: author_search,
    UserHome.menu_add_book: add_book,
    BookPhotoEntry.menu_label: add_book_from_photos,
    BatchBookEntry.menu_label: add_books_batch,
    UserHome.menu_edit_books: review_my_books
}

navigation_dict[selected_option]()