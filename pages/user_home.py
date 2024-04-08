import streamlit as st
from streamlit_option_menu import option_menu
from instruction_strings import home_intro
from utilities import check_authetication_status, hide

from pages.add_book import new_book_metadata

hide()
st.title("Data entry")

check_authetication_status()

st.write(home_intro)

st.write(
    "Before adding a book to our database, please search to check that we do not already have the book. You can also "
    "search by author to see which books that we have by them."
)

selected_option = option_menu(
    None, ["Search Books", "Search Authors", "Add a Book"],
    default_index=0,
    icons=['search', 'search', 'database-add'],
    menu_icon="cast", orientation="horizontal",
    key="user_option_menu"
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
            st.warning("No matching books found! Please ensure that the title is spelled correctly.")


def author_search():
    st.warning("Not implemented yet!")


# TODO: wire this up to store new book in Firestore!
def add_book():
    new_book_metadata()


navigation_dict = {
    "Search Books": book_search,
    "Search Authors": author_search,
    "Add a Book": add_book
}

navigation_dict[selected_option]()
