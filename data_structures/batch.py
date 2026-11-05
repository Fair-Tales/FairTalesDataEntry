"""Durable, bounded batch records; entity writes stay on the script thread."""
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists

from .base_structure import DataStructureBase, Field
from .book import Book


class BatchEntity(DataStructureBase):
    form_fields = {}
    ref_fields = []

    def __init__(self, db, record_id, db_object=None):
        self._db = db
        self._record_id = record_id
        super().__init__(self.collection, db_object)

    @property
    def document_id(self):
        return self._record_id

    def get_ref(self):
        return self._db.collection(self.collection).document(self.document_id)

    def update_record(self, field, value):
        if getattr(self, 'is_registered', False):
            self.get_ref().update({field: value, 'last_updated': datetime.now(timezone.utc)})

    def register(self, *, create_only=True):
        if self.is_registered:
            return
        now = datetime.now(timezone.utc)
        payload = {**self.to_dict(), 'is_registered': True, 'datetime_created': now}
        if create_only:
            self.get_ref().create(payload)
        else:
            self.get_ref().set(payload)
        self.reading_from_db = True
        try:
            self.is_registered = True
            self.datetime_created = now
        finally:
            self.reading_from_db = False

    def to_form(self):
        # Batch controls are rendered by add_books_batch, not a value-seeded form.
        return None

    @classmethod
    def load(cls, db, record_id):
        snap = db.collection(cls.collection).document(record_id).get()
        if not snap.exists:
            raise ValueError(f'Missing {cls.collection} record: {record_id}')
        return cls(db, record_id, snap.to_dict())

    def create_or_load(self):
        try:
            self.register()
        except AlreadyExists:
            return type(self).load(self._db, self.document_id)
        return self


class PhotoBatch(BatchEntity):
    collection = 'photo_batches'
    fields = {
        'is_registered': False, 'owner': '', 'entered_by': None,
        'datetime_created': None, 'last_updated': None, 'state': 'uploading',
        'backend': 'durable_v1', 'settings': {}, 'split_version': 'conservative_v1',
        'source_count': 0, 'group_count': 0, 'split_method': '', 'error': '',
    }
    for _name in fields:
        vars()[_name] = Field()


class BatchSource(BatchEntity):
    collection = 'photo_batch_sources'
    fields = {
        'is_registered': False, 'entered_by': None, 'datetime_created': None,
        'last_updated': None, 'batch_id': '', 'position': 0, 'name': '',
        'original_key': '', 'key': '', 'sha256': '', 'staged': False,
    }
    for _name in fields:
        vars()[_name] = Field()


class BatchGroup(BatchEntity):
    collection = 'photo_batch_groups'
    fields = {
        'is_registered': False, 'entered_by': None, 'datetime_created': None,
        'last_updated': None, 'batch_id': '', 'ordinal': 0, 'positions': [],
        'metadata': {}, 'metadata_state': 'pending', 'metadata_error': '',
        'book_id': '', 'job_id': '', 'state': 'review', 'error': '',
    }
    for _name in fields:
        vars()[_name] = Field()


class BatchBook(Book):
    """Additional provenance without changing the shared Book implementation."""
    fields = {**Book.fields, 'pipeline_job_id': '', 'batch_group_id': ''}
    pipeline_job_id = Field()
    batch_group_id = Field()
