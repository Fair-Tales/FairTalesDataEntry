import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions, old_books
from utilities import check_authentication_status, page_layout, FirestoreWrapper
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
                'Book', 'title', title
            )
            for title in titles
        ]
        print(books)

        if len(books) > 0:
            books = pd.concat(books)
            books.author = ['author']
                #' '.join([
                #    a.get().to_dict()['forename'],
                #    a.get().to_dict()['surname']
                #])
                #for a in books.author
            #]
            st.write('Results:')
            st.write(books[['title', 'author', 'publisher']])
        else:
            st.warning(Alerts.no_matching_book)
        # books = st.session_state.firestore.single_field_search(
        #     collection="books",
        #     field="title",
        #     contains_string=book_search_string
        # )
        # # TODO: combine author names...
        # if len(books) > 0:
        #     books.author = [
        #         a.get().to_dict()['surname']
        #         for a in books.author
        #     ]
        #
        #     st.write("Results:")
        #     st.write(books)
        #


def author_search():
    st.warning("Not implemented yet!")


def add_book():
    st.session_state['current_book'] = Book()
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
