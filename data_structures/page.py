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
        # Archival model regions/provisional labels and versioned projections.
        # Manual text remains the existing student-editable field; this sidecar
        # is never used to overwrite it or populated by a read-time migration.
        'extraction_evidence': None,
        # Whether an auto-corrected (cropped/rotated) image was written for this
        # page at processing time (#184). Recorded here so enter-text can decide
        # whether to offer the "show original" toggle WITHOUT an S3 HEAD request
        # on every render. ``None`` means "unknown" — a legacy page saved before
        # this field existed — for which enter-text falls back to the S3 check.
        'corrected': None,
        # Whether the automatic orientation check (#217) could NOT decide which
        # way up this page is (API error, unparseable reply, or an aspect-gate
        # disagreement — see image_processing.get_rotation_angle). No rotation
        # was applied in that case; enter-text surfaces a "check this page's
        # orientation" warning, and a manual crop-and-rotate save clears it.
        # Defaults False for legacy pages (missing key -> declared default).
        'rotation_uncertain': False,
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
        # Recovery state belongs to this object only and is never serialized.
        self.pending_edits = {}
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

    def save_edits(self, changes):
        """Persist text/story edits, retaining every unacknowledged field.

        Pending values must be recorded before a descriptor assignment: its
        database write can fail after the in-memory field has already changed.
        Persistence errors intentionally propagate to the recovery controller.
        """
        if self.pending_edits:
            raise ValueError("Resolve pending page edits before saving new edits")
        if set(changes) - {'text', 'contains_story'}:
            raise ValueError("Only text and contains_story edits are supported")
        self.pending_edits = dict(changes)
        if not self.pending_edits:
            return

        if not self.is_registered:
            for field, value in self.pending_edits.items():
                setattr(self, field, value)
            self.register(create_only=True)
            self.pending_edits.clear()
            return

        for field, value in tuple(self.pending_edits.items()):
            setattr(self, field, value)
            del self.pending_edits[field]

    def retry_pending_edits(self):
        """Retry pending persistence even when memory already has the value."""
        if not self.pending_edits:
            return
        if not self.is_registered:
            self.register(create_only=True)
            self.pending_edits.clear()
            return

        for field, value in tuple(self.pending_edits.items()):
            # A preceding field's failure may have prevented this assignment.
            # Restore local intent without depending on Field equality rules.
            previous_guard = self.reading_from_db
            self.reading_from_db = True
            try:
                setattr(self, field, value)
            finally:
                self.reading_from_db = previous_guard
            self.update_record(field=field, value=value)
            del self.pending_edits[field]

    def to_form(self):
        st.warning(Alerts.not_implemented)
