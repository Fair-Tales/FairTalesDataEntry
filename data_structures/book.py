import streamlit as st
from datetime import datetime
from utilities import author_entry_to_name, FirestoreWrapper


class Book:

    fields = {
        'title': "",
        'author': None,
        'character_count': -1,
        'page_count': -1,
        'word_count': -1,
        'sentence_count': -1,
        'datetime_created': -1,
        'entered_by': None,
        'entry_status': 'started',
        'first_content_page': -1,
        'last_content_page': -1,
        'illustrator': None,
        'publisher': None,
        'last_updated': -1,
        'published': 2024,
        'validated': False,
        'validated_by': None
    }

    form_fields = {
        'title': 'Title',
        'published': 'Date published'
    }

    def __init__(self, db_object=None):
        if db_object is None:
            for key in self.fields.keys():
                setattr(self, key, self.fields[key])

        else:
            for key in self.fields.keys():
                setattr(self, key, db_object[key])

    def to_dict(self, form_fields_only=False):
        fields_iterable = (
            self.form_fields.keys()
            if form_fields_only
            else self.fields.keys()
        )
        return {
                field: getattr(self, field)
                for field in fields_iterable
            }

    def to_form(self):

        author_dict = {
            author_entry_to_name(author): author.id
            for author in
            st.session_state.firestore.get_all_documents_stream(collection='authors')
        }

        st.header("Please enter metadata for the new book.")

        metadata = {
            key: None for key in ['title', 'author', 'illustrator', 'publisher', 'date_published']
        }
        # metadata['title'] = st.text_input("Title")
        self.title = st.text_input("Title", value=self.title)
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
            st.session_state['current_book'] = self
            st.session_state['active_form_to_confirm'] = 'new_book'
            st.switch_page("./pages/confirm_entry.py")

    def register(self):
        """ Sets entered_by user and records datetime. """
        self.entered_by = FirestoreWrapper().username_to_doc_ref(
            st.session_state['username']
        )
        self.datetime_created = datetime.now()

    def save_to_db(self):
        db = FirestoreWrapper().connect()
        db.collection('books').document(self.title).set(self.to_dict(), merge=True)
