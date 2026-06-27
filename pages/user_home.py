import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions, old_books, BookPhotoEntry
from utilities import check_authentication_status, page_layout, navigate_to, clear_page_history
from data_structures import Book
import pandas as pd

check_authentication_status()

# TODO: migrate to a proper search service like Algolia?
def book_search():
    book_search_string = st.text_input(
        "Search by book title — enter a full or partial title and press Enter to find close matches.",
        value="",
        help="You can enter either all or part of the title."
    )
    if len(book_search_string) > 0:
        search_term = book_search_string.lower()

        matching_titles = [
            title for title in st.session_state['book_dict'].keys()
            if search_term in title.lower()
        ]

        if len(matching_titles) == 0:
            st.warning(Alerts.no_matching_book)
        else:
            st.write(f"Results ({len(matching_titles)} found):")
            for title in matching_titles:
                book_ref = st.session_state['book_dict'][title]
                book_data = book_ref.get().to_dict()

                author_ref = book_data.get('author')
                if author_ref:
                    author_data = author_ref.get().to_dict()
                    author_name = ' '.join([
                        author_data.get('forename', ''),
                        author_data.get('surname', '')
                    ]).strip() or "Unknown"
                else:
                    author_name = "Unknown"

                published = book_data.get('published', '')
                year_str = f" ({published})" if published else ""

                with st.expander(f"{title}{year_str}  —  {author_name}"):
                    publisher_ref = book_data.get('publisher')
                    if publisher_ref:
                        publisher_data = publisher_ref.get().to_dict()
                        publisher_name = publisher_data.get('name', 'Unknown')
                    else:
                        publisher_name = "Unknown"

                    illustrator_ref = book_data.get('illustrator')
                    if illustrator_ref:
                        illustrator_data = illustrator_ref.get().to_dict()
                        illustrator_name = ' '.join([
                            illustrator_data.get('forename', ''),
                            illustrator_data.get('surname', '')
                        ]).strip() or "Unknown"
                    else:
                        illustrator_name = "Unknown"

                    st.write(f"**Publisher:** {publisher_name}")
                    st.write(f"**Illustrator:** {illustrator_name}")

def author_search():
    author_search_string = st.text_input(
        "Search by author name — enter a full or partial name and press Enter to find close matches.",
        value="",
        help="You can enter either all or part of the name."
    )
    if len(author_search_string) > 0:
        search_term = author_search_string.lower()

        matching_names = [
            full_name for full_name in st.session_state['author_dict'].keys()
            if search_term in full_name.lower()
        ]

        if len(matching_names) == 0:
            st.warning(Alerts.no_matching_author)
        else:
            st.write(f"Results ({len(matching_names)} found):")
            for full_name in matching_names:
                author_ref = st.session_state['author_dict'][full_name]
                author_data = author_ref.get().to_dict()

                birth_year = author_data.get('birth_year')
                birth_year_str = str(birth_year) if birth_year and birth_year > 0 else "Unknown"
                gender = author_data.get('gender') or "Not recorded"

                with st.expander(f"{full_name}  —  b. {birth_year_str}  |  {gender}"):
                    books_df = st.session_state.firestore.get_by_field(
                        collection='books',
                        field='author',
                        match=author_ref
                    )
                    if books_df.empty:
                        st.write("No books found for this author.")
                    else:
                        st.write("**Books:**")
                        for _, book_row in books_df.iterrows():
                            title = book_row.get('title', 'Unknown title')
                            published = book_row.get('published', '')
                            year_str = f" ({published})" if published else ""
                            st.write(f"- {title}{year_str}")


def add_book():
    st.session_state['current_book'] = Book()
    # Clear any leftover entity selections / flow flag from a previous (cancelled)
    # book entry so the new book form starts blank.
    for _key in ('current_author', 'current_illustrator', 'current_publisher', 'adding_book_entries'):
        st.session_state.pop(_key, None)
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
    ):
        st.session_state.pop(_key, None)
    navigate_to("./pages/add_book_photos.py")

def review_my_books():
    navigate_to("./pages/review_my_books.py")

clear_page_history()
page_layout(current_page="./pages/user_home.py")

st.title("Fair Tales Data Entry Tool")

st.write(Instructions.home_intro)
st.write(Instructions.advise_to_search)

selected_option = option_menu(
    None,
    ["Search Books", "Search Authors", "Add a Book", BookPhotoEntry.menu_label, "Edit my Books"],
    default_index=0,
    icons=['search', 'search', 'database-add', 'camera', 'pencil-square'],
    menu_icon="cast", orientation="horizontal",
    key="user_option_menu",
    styles={
        "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
        "nav-link-selected": {"background-color": "green"},
    }
)

navigation_dict = {
    "Search Books": book_search,
    "Search Authors": author_search,
    "Add a Book": add_book,
    BookPhotoEntry.menu_label: add_book_from_photos,
    "Edit my Books": review_my_books
}

navigation_dict[selected_option]()