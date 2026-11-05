"""Immutable audit of emitted surfaces and accepted/rejected review decisions."""
import hashlib
import json

from alias_schema import ALIAS_SCHEMA_VERSION
from .base_structure import DataStructureBase, Field


class AliasReviewAudit(DataStructureBase):
    fields = {
        'is_registered': False, 'book_id': '', 'emitted': [], 'decisions': [],
        'raw_response': None, 'alias_schema_version': ALIAS_SCHEMA_VERSION,
        'datetime_created': -1, 'last_updated': -1, 'entered_by': None,
    }
    for field in fields:
        if field not in DataStructureBase.base_class_fields + ['is_registered']:
            vars()[field] = Field()
    form_fields = {}
    ref_fields = []

    def __init__(self, book_id, emitted, decisions, raw_response=None):
        super().__init__('alias_review_audits')
        self.book_id, self.emitted = book_id, emitted
        self.decisions, self.raw_response = decisions, raw_response

    @property
    def document_id(self):
        payload = json.dumps([self.book_id, self.emitted, self.decisions, self.raw_response],
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return 'alias-review-v1-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def to_form(self):
        """Audit records are immutable and have no editing form."""
