import streamlit as st
from datetime import datetime, timezone
import numpy as np
from abc import ABC, abstractmethod
from google.cloud.firestore_v1 import LastUpdateOption


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
        if instance is None:
            return self
        return instance.__dict__[self._name]

    def __set__(self, instance, value):

        if (not instance.reading_from_db and self._name in self.ref_field_setters
                and isinstance(value, str)):
            value = st.session_state.get(self.ref_field_setters[self._name], {}).get(value)

        hook = getattr(instance, "_assign_field_bundle", None)
        if not instance.reading_from_db and hook is not None and hook(self._name, value):
            return

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
        self._update_time = db_object.get("_update_time") if db_object else None
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
                reference = getattr(self, field)
                return reference.rsplit('/', 1)[-1] if isinstance(reference, str) else reference.id
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

    def register(self, *, create_only=False):
        """Save an initial document and acknowledge registration after success.

        Ordinary callers retain merge semantics. Missing-page recovery requests
        ``create_only`` so a competing save can never be overwritten.
        """
        if self.is_registered:
            return

        entered_by = st.session_state['firestore'].username_to_doc_ref(
            st.session_state['username']
        )
        created_at = datetime.now(timezone.utc)
        payload = {
            **self.to_dict(),
            'entered_by': entered_by,
            'datetime_created': created_at,
            'is_registered': True,
        }
        result = self.save_to_db(data=payload, create_only=create_only)
        self._update_time = getattr(result, "update_time", None)

        previous_guard = self.reading_from_db
        self.reading_from_db = True
        try:
            self.entered_by = entered_by
            self.datetime_created = created_at
            self.is_registered = True
        finally:
            self.reading_from_db = previous_guard

    def register_batched(self, batch, *, create_only=False):
        """Stage a write, returning an acknowledgement to run after commit.

        A failed batch must leave the entity unregistered. The caller invokes
        the returned callback only after the entire batch commits successfully.
        """
        if self.is_registered:
            return lambda: None
        payload = {
            **self.to_dict(),
            'entered_by': st.session_state['firestore'].username_to_doc_ref(
                st.session_state['username']),
            'datetime_created': datetime.now(timezone.utc),
            'is_registered': True,
        }
        if create_only:
            batch.create(self.get_ref(), payload)
        else:
            batch.set(self.get_ref(), payload, merge=True)

        def acknowledge():
            self._adopt_fields(payload)
        return acknowledge

    def _adopt_fields(self, values):
        """Load an acknowledged atomic write through descriptors without I/O."""
        previous_guard = self.reading_from_db
        self.reading_from_db = True
        try:
            for key, value in values.items():
                if key in self.fields:
                    setattr(self, key, value)
        finally:
            self.reading_from_db = previous_guard

    def update_fields_conditionally(self, updates, *, expected_update_time,
                                    expected_raw_hash=None, firestore_client=None):
        """One compare-and-swap write through the shared entity layer.

        The server update-time precondition rejects concurrent raw edits *and*
        overrides. A source hash check alone would leave a read/write race.
        No client, snapshot read, or retry is implicit when one is injected.
        """
        if expected_update_time is None:
            raise ValueError("A snapshot update_time is required for conditional updates")
        if any(key not in self.fields for key in updates):
            raise ValueError("Conditional update includes an undeclared entity field")
        if expected_raw_hash is not None:
            from alias_schema import raw_surface_hash
            if raw_surface_hash(getattr(self, 'name', '')) != expected_raw_hash:
                raise ValueError("Raw surface changed after the proposed projection")
        validate = getattr(self, '_validate_bundle_update', None)
        if validate is not None:
            validate(updates)
        ref = (firestore_client.collection(self.belongs_to_collection).document(self.document_id)
               if firestore_client is not None else self.get_ref())
        result = ref.update(dict(updates), option=LastUpdateOption(expected_update_time))
        self._adopt_fields(updates)
        self._update_time = getattr(result, 'update_time', None)
        return result

    def save_to_db(self, *, data=None, create_only=False):
        payload = self.to_dict() if data is None else data
        ref = self.get_ref()
        if create_only:
            return ref.create(payload)
        else:
            return ref.set(payload, merge=True)
