import streamlit as st
from datetime import datetime, timezone
import numpy as np
from abc import ABC, abstractmethod


class Field:

    # Allow reference fields to be set by passing a string
    # that is a key to relevant lookup dictionary:
    ref_field_setters = {
        'author': 'author_dict',
        'illustrator': 'illustrator_dict',
        'publisher': 'publisher_dict',
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
                setattr(self, key, self._default_for(key))

        else:
            self.reading_from_db = True
            for key in self.fields.keys():
                if key in db_object and not self._is_missing(db_object[key]):
                    setattr(self, key, self.safe_cast(db_object[key]))
                else:
                    # Backward compatibility: documents written before a field
                    # was added to this structure won't contain the key — and a
                    # document read via pandas surfaces a missing field as NaN (a
                    # float) rather than an absent key. In both cases fall back to
                    # the declared default rather than raising KeyError or storing
                    # NaN (which breaks code expecting the declared type, e.g.
                    # iterating Book.characters), so older records stay readable.
                    setattr(self, key, self._default_for(key))
            self.reading_from_db = False

    def _default_for(self, key):
        """Return a fresh copy of the declared default for ``key``.

        Mutable defaults (e.g. a list-of-references field such as Book's
        ``characters``) must be copied per instance, otherwise every object of
        the class would share — and mutate — the same underlying container.
        """
        default = self.fields[key]
        if isinstance(default, (list, dict, set)):
            return type(default)(default)
        return default

    @staticmethod
    def safe_cast(value):
        if isinstance(value, np.int64):
            return int(value)
        elif isinstance(value, np.bool_):
            return bool(value)
        else:
            return value

    @staticmethod
    def _is_missing(value):
        """True if ``value`` represents a missing field.

        A document read via pandas surfaces a missing field as NaN (a float)
        rather than an absent key. Stored verbatim that breaks any code
        expecting the declared type (e.g. iterating ``Book.characters``), so we
        treat NaN as "field not present" and fall back to the declared default.
        """
        return isinstance(value, float) and np.isnan(value)

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
        db = st.session_state.firestore.connect_book()
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

    def register_batched(self, batch):
        """Like :meth:`register`, but stage the initial full write into ``batch``
        instead of committing it immediately (#184).

        Preserves the write-through ``Field`` semantics: the object must be built
        UNREGISTERED (so field assignments never hit Firestore), and this single
        batched ``set`` replaces ``register``'s ``save_to_db``. The caller commits
        ``batch`` once for many entities, turning N per-entity round-trips into one.
        Sets the same ``entered_by``/``datetime_created``/``is_registered`` fields
        as ``register`` so the persisted document is identical.
        """
        if not self.is_registered:
            self.entered_by = st.session_state['firestore'].username_to_doc_ref(
                st.session_state['username']
            )
            self.datetime_created = datetime.now(timezone.utc)
            self.is_registered = True
            batch.set(self.get_ref(), self.to_dict(), merge=True)

    def save_to_db(self):
        db = st.session_state['firestore'].connect_book()
        db.collection(
            self.belongs_to_collection
        ).document(self.document_id).set(self.to_dict(), merge=True)
