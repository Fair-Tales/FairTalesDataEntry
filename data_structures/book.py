import streamlit as st
from utilities import author_entry_to_name
from text_content import Instructions, BookForm
from .base_structure import DataStructureBase, Field
from .author import Author


class Book(DataStructureBase):

    author = Field()

    fields = {
        'is_registered': False,
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
        'validated_by': None,
        'photos_uploaded': False,
        'photos_url': ""
    }

    form_fields = {
        'title': 'Title',
        'published': 'Date published',
        'author': 'Author'
    }

    ref_fields = ['author', 'entered_by']  # Reference fields will display document ID for human consumption

    def __init__(self, db_object=None):
        super().__init__(collection='books', db_object=db_object)
        # self.author_name = None

    @property
    def document_id(self):
        return self.title.lower().replace(" ", "_")

    def to_form(self):
        st.header(BookForm.header)

        self.title = st.text_input("Title", value=self.title)
        self.published = st.number_input(
            "Date published", min_value=1900, max_value=2024, value=self.published
        )
        st.write(Instructions.author_publisher_illustrator_select)

        author_options = ["None of these (create a new author now)."] + list(
            st.session_state['author_dict'].keys()
        )
        author_index = (
            author_options.index(author_entry_to_name(self.author.get()))
            if self.author is not None and author_entry_to_name(self.author.get()) in author_options
            else 0
        )

        self.author = st.selectbox(
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
            st.session_state['current_book'] = self

            if self.author is None:
                st.session_state['current_author'] = Author()
                st.switch_page("./pages/add_author.py")
            else:
                if self.is_registered:
                    if st.session_state.current_book.photos_uploaded:
                        st.switch_page("./pages/enter_text.py")
                    else:
                        st.switch_page("./pages/page_photo_upload.py")
                else:
                    st.session_state['active_form_to_confirm'] = 'new_book'
                    st.switch_page("./pages/confirm_entry.py")
