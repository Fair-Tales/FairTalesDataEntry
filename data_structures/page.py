import streamlit as st
from text_content import Instructions, Alerts
from .base_structure import DataStructureBase, Field


class Page(DataStructureBase):

    book = Field()

    fields = {
        'contains_story': False,
        'book': None,
        'page_number': -1,
        'text': "",
        'datetime_created': -1,
        'entered_by': None,
        'is_registered': False,
        'last_updated': -1
    }

    form_fields = {}
    ref_fields = ['book', 'entered_by']  # Reference fields will display document ID for human consumption

    def __init__(self, db_object=None, page_number=None, book=None):
        super().__init__(collection='pages', db_object=db_object)
        if db_object is None:
            self.page_number = page_number
            self.book = book

    @property
    def document_id(self):
        return f"{self.book.get().id}_{self.page_number}"

    def to_form(self):
        st.warning(Alerts.not_implemented)
