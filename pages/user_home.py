import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions, old_books
from utilities import check_authentication_status, page_layout
from data_structures import Book
import pandas as pd

check_authentication_status()

# TODO: migrate to a proper search service like Algolia?
def book_search():
    book_search_string = st.text_input(
        "Search our database by book title.",
        value="",
        help="You can enter either all or part of the title."
    )
    if len(book_search_string) > 0:

        search_title = book_search_string.lower()

        #old_titles = [
        #    title for title in old_books
        #    if book_search_string.lower() in title.lower()
        #]
        #if len(old_titles) > 0:
        #    st.write(f"These titles were found from the original dataset: {old_titles}")

        #else:
        titles = [
            title for title in st.session_state['book_dict'].keys()
            if search_title in title.lower()
        ]

        books = [
            st.session_state.firestore.get_by_field(
                'books', 'title', title
            )
            for title in titles
        ]

        if len(books) > 0:
            books = pd.concat(books)
            books.author = [
                ' '.join([
                    a.get().to_dict()['forename'],
                    a.get().to_dict()['surname']
                ])
                for a in books.author
            ]
            books.publisher = [
                a.get().to_dict()['name']
                for a in books.publisher
                ]
            books.illustrator = [
                ' '.join([
                    a.get().to_dict()['forename'],
                    a.get().to_dict()['surname']
                ])
                for a in books.illustrator
            ]
            st.write('Results:')
            st.write(books[['title', 'author', 'publisher', 'illustrator']])
        else:
            st.warning(Alerts.no_matching_book)
        # # TODO: combine author names...
        # if len(books) > 0:
        #     books.author = [
        #         a.get().to_dict()['surname']
        #         for a in books.author
        #     ]
        #
        #     st.write("Results:")
        #     st.write(books)

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
    st.switch_page("./pages/add_book.py")

def review_my_books():
    st.switch_page("./pages/review_my_books.py")

page_layout()

st.title("Fair Tales Data Entry Tool")

st.write(Instructions.home_intro)
st.write(Instructions.advise_to_search)

selected_option = option_menu(
    None, ["Search Books", "Search Authors", "Add a Book", "Edit my Books"],
    default_index=0,
    icons=['search', 'search', 'database-add', 'pencil-square'],
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
    "Edit my Books": review_my_books
}

navigation_dict[selected_option]()