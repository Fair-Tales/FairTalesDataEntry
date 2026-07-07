import streamlit as st
from text_content import Instructions, Alerts
from .base_structure import DataStructureBase, Field


class Page(DataStructureBase):

    fields = {
        'is_registered': False,
        'book': None,
        'page_number': -1,
        'contains_story': False,
        'text': "",
        # Whether an auto-corrected (cropped/rotated) image was written for this
        # page at processing time (#184). Recorded here so enter-text can decide
        # whether to offer the "show original" toggle WITHOUT an S3 HEAD request
        # on every render. ``None`` means "unknown" — a legacy page saved before
        # this field existed — for which enter-text falls back to the S3 check.
        'corrected': None,
        'datetime_created': -1,
        'entered_by': None,
        'last_updated': -1
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {}
    ref_fields = ['book', 'entered_by']  # Reference fields will display document ID for human consumption

    def __init__(self, db_object=None, page_number=None, book=None):
        super().__init__(collection='pages', db_object=db_object)
        if db_object is None:
            self.page_number = page_number
            self.book = book

    @property
    def document_id(self):
        # ``self.book`` is a DocumentReference whose ``.id`` is available locally;
        # use it directly rather than ``.get().id``, which makes a Firestore
        # round-trip on every access of this hot-path property (#128).
        return f"{self.book.id}_{self.page_number}"

    def to_form(self):
        st.warning(Alerts.not_implemented)
