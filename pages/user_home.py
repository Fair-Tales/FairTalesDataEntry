import streamlit as st
from streamlit_option_menu import option_menu
from text_content import Alerts, Instructions
from utilities import check_authentication_status, hide
from data_structures import Book


hide()
st.title("SAW data entry tool")

check_authentication_status()

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


# TODO: fix handling of empty results...
# TODO: fix search for contains (crrently not robus because not supported by Firestore!!)
#    try another search option like Algolia: https://firebase.google.com/docs/firestore/solutions/search
#    https://www.algolia.com/pricing/
# TODO: get author from reference (see here: https://stackoverflow.com/questions/46878913/cloud-firestore-how-to-fetch-a-document-reference-inside-my-collection-query-an)
# TODO: also currently case sensitive! Algolia will fix...
def book_search():
    book_search_string = st.text_input(
        "Search our database by book title.",
        value="",
        help="You can enter either all or part of the title."
    )
    if len(book_search_string) > 0:
        books = st.session_state.firestore.single_field_search(
            collection="books",
            field="title",
            contains_string=book_search_string
        )
        # TODO: combine author names...
        if len(books) > 0:
            books.author = [
                a.get().to_dict()['surname']
                for a in books.author
            ]

            st.write("Results:")
            st.write(books)

        else:
            st.warning(Alerts.no_matching_book)


def author_search():
    st.warning("Not implemented yet!")


def add_book():
    st.session_state['current_book'] = Book()
    st.switch_page("./pages/add_book.py")


def review_my_books():
    st.switch_page("./pages/review_my_books.py")


navigation_dict = {
    "Search Books": book_search,
    "Search Authors": author_search,
    "Add a Book": add_book,
    "Edit my Books": review_my_books
}

navigation_dict[selected_option]()
