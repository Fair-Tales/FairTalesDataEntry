import streamlit as st

from text_content import Alerts
from .base_structure import DataStructureBase, Field


class Collection(DataStructureBase):
    """A named set of book references (issue #75).

    A collection groups Firestore book references so the results dashboard can be
    scoped to that set. Predefined collections (e.g. a primary-school's library,
    or a per-user saved selection) live in the ``collections`` Firestore
    collection and follow the standard write-through ``Field`` pattern: assigning
    a field on a *registered* collection persists that single field, while
    ``register()`` performs the initial full save.

    The ``books`` field is a list of Firestore ``DocumentReference`` objects (the
    same reference type stored in ``st.session_state['book_dict']``). It is NOT a
    single-reference field, so it is deliberately excluded from ``ref_fields``
    (whose machinery resolves/serialises a *single* reference). The results
    dashboard's ``_book_id`` helper accepts either a reference or an id string,
    so either representation in the list works downstream.
    """

    fields = {
        'is_registered': False,
        # Display name of the collection (e.g. "St Mary's Primary").
        'name': "",
        # Optional scope/ownership label: a school name or a username. Empty for
        # a global/unscoped collection.
        'owner': "",
        # List of Firestore references to the book documents in this collection.
        'books': [],
        'datetime_created': -1,
        'last_updated': -1,
        'entered_by': None,
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name',
        'owner': 'Owner / scope',
    }

    # ``books`` is a *list* of references, not a single reference, so it is not a
    # ref_field (the ref_field machinery calls ``.get().id`` on a single ref).
    ref_fields = []

    def __init__(self, db_object=None):
        super().__init__(collection='collections', db_object=db_object)

    @property
    def document_id(self):
        """Stable id derived from owner + name.

        Scoping the id by owner lets two schools each keep a "Year 1" collection
        without clobbering one another; an unscoped collection keys on name only.
        """
        owner_slug = (self.owner or "").strip().lower().replace(" ", "_")
        name_slug = (self.name or "").strip().lower().replace(" ", "_")
        return f"{owner_slug}_{name_slug}" if owner_slug else name_slug

    @property
    def book_refs(self):
        """The collection's book references as a list (tolerant of legacy data).

        Older/odd documents may store a non-list under ``books``; treat anything
        that is not a list as "no books" so callers can iterate safely.
        """
        refs = self.books
        return refs if isinstance(refs, list) else []

    def to_form(self):
        # Collections are assembled and saved by the collection-picker page
        # (pages/collection_picker.py), which drives ``register()`` directly with
        # the books the user has selected. A standalone generic form is not used.
        st.warning(Alerts.not_implemented)
