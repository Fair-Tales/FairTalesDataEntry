import streamlit as st
from datetime import datetime
import numpy as np
from abc import ABC, abstractmethod


class DataStructureBase(ABC):

    def __init__(self, db_object=None):
        if db_object is None:
            for key in self.fields.keys():
                setattr(self, key, self.fields[key])

        else:
            for key in self.fields.keys():
                setattr(self, key, self.safe_cast(db_object[key]))

    @staticmethod
    def safe_cast(value):
        if isinstance(value, np.int64):
            return int(value)
        elif isinstance(value, np.bool_):
            return bool(value)
        else:
            return value

    def get_field(self, field, convert_ref_fields_to_ids=False):
        if convert_ref_fields_to_ids and field in self.ref_fields:
            if getattr(self, field) is None:
                return None
            else:
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

    @abstractmethod
    def to_form(self):
        pass

    @property
    @abstractmethod
    def document_id(self):
        pass

    def get_ref(self):
        db = st.session_state.firestore.connect()
        return db.collection(self.belongs_to_collection).document(self.document_id)

    def register(self):
        """ Sets entered_by user and records datetime if not set. """
        if self.datetime_created == -1:
            self.entered_by = st.session_state['firestore'].username_to_doc_ref(
                st.session_state['username']
            )
            self.datetime_created = datetime.now()

    def save_to_db(self):
        db = st.session_state['firestore'].connect()
        db.collection(
            self.belongs_to_collection
        ).document(self.document_id).set(self.to_dict(), merge=True)
