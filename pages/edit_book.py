import streamlit as st
from utilities import author_entry_to_name, hide

hide()


# TODO: wire this up with session state so that the form is prepopulated if editing an already entered book.

def new_book_metadata():

    # first find existing authors:
    author_dict = {
        author_entry_to_name(author): author.id
        for author in
        st.session_state.firestore.get_all_documents_stream(collection='authors')
    }

    with st.form("new_book"):
        st.header("Please enter metadata for the new book.")

        metadata = {
            key: None for key in ['title', 'author', 'illustrator', 'publisher', 'date_published']
        }
        metadata['title'] = st.text_input("Title")
        metadata['date_published'] = st.number_input(
            "Date published", min_value=1900, max_value=2024, value=2023
        )
        st.write(
            """
            Please select author, publisher and illustrator. 
            If not listed, please select `None of these` and you will be guided 
            to enter these details on the next step.
            """
        )
        metadata['author'] = st.selectbox(
            "Select from existing authors",
            options=["None of these (create a new author now)."] + list(author_dict.keys())
        )
        metadata['publisher'] = st.selectbox(
            "Select from existing publishers", options=["None of these (create a new publisher)."] + []
        )
        metadata['illustrator'] = st.selectbox(
            "Select from existing illustrators", options=["None of these (create a new illustrator)."] + []
        )
        submitted = st.form_submit_button("Submit")
        if submitted:
            st.session_state['book_metadata'] = metadata
            st.session_state['active_form_to_confirm'] = 'new_book'
            st.switch_page("./pages/confirm_entry.py")


new_book_metadata()

cancel_button = st.button("Cancel entering new book.")

if cancel_button:
    st.switch_page("./pages/user_home.py")
