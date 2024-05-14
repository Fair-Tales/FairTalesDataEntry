import streamlit as st
from datetime import datetime
from utilities import author_entry_to_name, FirestoreWrapper
from text_content import Instructions


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
        'published': 2012,
        'validated': False,
        'validated_by': None
    }

    form_fields = {
        'title': 'Title',
        'published': 'Date published',
        'author': 'Author'
    }

    ref_fields = ['author', 'entered_by']  # Reference fields will display document ID for human consumption

    def __init__(self, db_object=None):
        if db_object is None:
            for key in self.fields.keys():
                setattr(self, key, self.fields[key])

        else:
            for key in self.fields.keys():
                setattr(self, key, db_object[key])

        self.author_name = None
        self.author_dict = {}

    def get_field(self, field, convert_ref_fields_to_ids=False):
        if convert_ref_fields_to_ids and field in self.ref_fields:
            return getattr(self, field).get().id
        else:
            return getattr(self, field)

    def to_dict(self, form_fields_only=False, convert_ref_fields_to_ids=False):
        fields_iterable = (
            self.form_fields.keys()
            if form_fields_only
            else self.fields.keys()
        )
        return {
                field: self.get_field(field, convert_ref_fields_to_ids)
                for field in fields_iterable
            }

    def to_form(self):
# TODO: refactor this to reduce number of queries! (e.g. store author_dict once and update it manually)
        self.author_dict = {
            author_entry_to_name(author): author.reference
            for author in
            st.session_state.firestore.get_all_documents_stream(collection='authors')
        }

        st.header("Please enter metadata for the new book.")

        self.title = st.text_input("Title", value=self.title)
        self.published = st.number_input(
            "Date published", min_value=1900, max_value=2024, value=self.published
        )
        st.write(Instructions.author_publisher_illustrator_select)

        author_options = ["None of these (create a new author now)."] + list(self.author_dict.keys())
        author_index = (
            author_options.index(author_entry_to_name(self.author.get()))
            if self.author is not None and author_entry_to_name(self.author.get()) in author_options
            else 0
        )

        self.author_name = st.selectbox(
            "Select from existing authors",
            options=author_options,
            index=author_index
        )

# TODO: for publisher/illustrator as for author
        self.publisher = st.selectbox(
            "Select from existing publishers", options=["None of these (create a new publisher)."] + []
        )
        self.illustrator = st.selectbox(
            "Select from existing illustrators", options=["None of these (create a new illustrator)."] + []
        )
        submitted = st.form_submit_button("Submit")

        if submitted:
            self.author = self.author_dict.get(self.author_name, None)
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
        db.collection('books').document(self.title.lower()).set(self.to_dict(), merge=True)
