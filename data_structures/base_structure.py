import streamlit as st
from datetime import datetime, timezone
import numpy as np
from abc import ABC, abstractmethod


class Field:

    # Allow reference fields to be set by passing a string
    # that is a key to relevant lookup dictionary:
    ref_field_setters = {
        'author': 'author_dict',
        'book': 'book_dict',
        'character': 'character_dict',
    }

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, instance, owner):
        return instance.__dict__[self._name]

    def __set__(self, instance, value):

        if self._name in self.ref_field_setters.keys() and isinstance(value, str):
            value = st.session_state[self.ref_field_setters[self._name]].get(value, None)

        instance.__dict__[self._name] = value

        if not instance.reading_from_db:
            instance.update_record(
                field=self._name,
                value=value
            )


class DataStructureBase(ABC):
    """
    Note: is_registered is special attribute to flag if object has been saved to the database.
    All other attributes are handled by the Field descriptor and update the database
    on __set__ if is_registered is True.
    """
    base_class_fields = ['last_updated', 'entered_by', 'datetime_created']
    for v in base_class_fields:
        vars()[v] = Field()

    def __init__(self, collection, db_object=None):
        self.belongs_to_collection = collection
        self.reading_from_db = False

        if db_object is None:
            for key in self.fields.keys():
                setattr(self, key, self.fields[key])

        else:
            self.reading_from_db = True
            for key in self.fields.keys():
                setattr(self, key, self.safe_cast(db_object[key]))
            self.reading_from_db = False

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

    def update_record(self, field, value):
        if self.is_registered:
            st.session_state.firestore.update_field(
                collection=self.belongs_to_collection,
                document=self.document_id,
                field=field,
                value=value
            )
            if field != 'last_updated':
                self.last_updated = datetime.now(timezone.utc)

    def get_ref(self):
        db = st.session_state.firestore.connect()
        return db.collection(self.belongs_to_collection).document(self.document_id)

    def register(self):
        """ Sets entered_by user and records datetime if not set. """
        if not self.is_registered:
            self.entered_by = st.session_state['firestore'].username_to_doc_ref(
                st.session_state['username']
            )
            self.datetime_created = datetime.now(timezone.utc)
            self.is_registered = True
            self.save_to_db()

    def save_to_db(self):
        db = st.session_state['firestore'].connect()
        db.collection(
            self.belongs_to_collection
        ).document(self.document_id).set(self.to_dict(), merge=True)
