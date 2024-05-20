import streamlit as st
from text_content import Instructions, Alerts
from .base_structure import DataStructureBase


class Page(DataStructureBase):

    fields = {
        'contains_story': False,
        'book': None,
        'page_number': -1,
        'text': "",
        'datetime_created': -1,
        'entered_by': None
    }

    form_fields = {}
    ref_fields = ['book', 'entered_by']  # Reference fields will display document ID for human consumption

    def __init__(self, db_object=None, page_number=None, book=None):
        self._book = None
        super().__init__(db_object=db_object)
        self.belongs_to_collection = 'pages'

    @property
    def document_id(self):
        return f"{self.book.get().id}_{self.page_number}"

    @property
    def book(self):
        return self._book

    @book.setter
    def book(self, value):
        if isinstance(value, str):
            self.set_book(book_title=value)
        else:
            self._book = value

    def to_form(self):
        st.warning(Alerts.not_implemented)

    def set_book(self, book_title):
        self._book = st.session_state['book_dict'].get(book_title, None)
