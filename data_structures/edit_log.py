"""Append-only edit-audit log (issue #47, Part B).

``EditLog`` records every correction a validator makes while reviewing a
submitted book, capturing the ORIGINAL value and the CHANGE. The records are
intended as TRAINING DATA for future AI correction systems, so each row pins
down exactly what changed, on which entity, by whom, and when.

DATA-MODEL CHOICE — deliberate raw-dict writer (NOT ``DataStructureBase``/``Field``)
---------------------------------------------------------------------------------
The project convention is that Firestore-backed entities subclass
``DataStructureBase`` and use write-through ``Field`` descriptors. That pattern
models a *mutable* entity edited through a form, where assigning one attribute
writes that single field back to a document whose ``document_id`` is derived
from its content.

An audit record is the opposite shape, so the pattern does not fit:
  * it is APPEND-ONLY and IMMUTABLE — written once and never edited
    field-by-field, so per-field write-through buys nothing;
  * it has NO natural deterministic ``document_id`` — the same
    (book, entity, field) tuple recurs over time, so an auto-generated id
    (Firestore ``add()``) is the correct key, not a content-derived one;
  * records are produced in BATCHES during a diff, not bound to a ``to_form()``
    widget.

Forcing it into ``DataStructureBase`` would mean inventing an artificial
``document_id`` and a no-op ``to_form()``/``form_fields``. So, like the
documented ``User`` raw-dict exception, ``EditLog`` is a deliberate, justified
raw-dict writer. The decision is recorded in ``DECISIONS.md`` (005).

Schema of an ``edit_log`` document
----------------------------------
  book_id      (str)                — the reviewed book's document id
  book_title   (str)                — denormalised for human-readable reports
  entity_type  (str)                — 'book' | 'page' | 'character' | 'alias'
  entity_id    (str)                — the edited entity's document id
                                      (equals book_id when entity_type == 'book')
  field        (str)                — the field that changed (e.g. 'text')
  old_value    (scalar)             — the ORIGINAL value (archivist's entry)
  new_value    (scalar)             — the validator's corrected value
  edited_by    (DocumentReference)  — the validator's user document
  entered_by   (str|ref)            — who ORIGINALLY entered the book (the book's
                                      own entered_by, denormalised for self-
                                      contained training data)
  timestamp    (datetime, UTC)      — when the correction was recorded
  context      (str)                — 'validation'
"""

import streamlit as st
from datetime import datetime, timezone


class EditLog:

    COLLECTION = 'edit_log'
    CONTEXT_VALIDATION = 'validation'

    # The entity types an audit record may describe.
    ENTITY_BOOK = 'book'
    ENTITY_PAGE = 'page'
    ENTITY_CHARACTER = 'character'
    ENTITY_ALIAS = 'alias'

    @staticmethod
    def _coerce(value):
        """Coerce a value into something Firestore can store and a human can read.

        Native scalars (``str``/``int``/``float``/``bool``/``None``) pass through
        unchanged so booleans and years stay typed in the log. A Firestore
        ``DocumentReference`` is recorded as its ``path`` string (stable and
        unambiguous), and anything else falls back to ``str`` so a record can
        never fail to serialise.
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        path = getattr(value, 'path', None)
        if path is not None:
            return path
        return str(value)

    @classmethod
    def record(cls, *, book_id, book_title, entity_type, entity_id, field,
               old_value, new_value, edited_by, entered_by=None,
               context=CONTEXT_VALIDATION):
        """Write a single before/after audit record to the ``edit_log`` collection.

        ``old_value``/``new_value`` are coerced to serialisable scalars (see
        ``_coerce``). ``edited_by`` should be the validator's user
        ``DocumentReference`` (stored as a reference, mirroring ``entered_by``
        elsewhere). ``entered_by`` is who ORIGINALLY entered the book (the book's
        own ``entered_by``), denormalised here too — already in the book data, but
        duplicated onto each record so the audit log is self-contained training
        data; coerced via ``_coerce`` (a reference becomes its path string).
        Returns the created ``DocumentReference``.
        """
        return st.session_state['firestore'].add_document(
            collection=cls.COLLECTION,
            data={
                'book_id': book_id,
                'book_title': book_title,
                'entity_type': entity_type,
                'entity_id': entity_id,
                'field': field,
                'old_value': cls._coerce(old_value),
                'new_value': cls._coerce(new_value),
                'edited_by': edited_by,
                'entered_by': cls._coerce(entered_by),
                'timestamp': datetime.now(timezone.utc),
                'context': context,
            },
        )
