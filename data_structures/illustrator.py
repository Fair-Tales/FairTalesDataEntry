import streamlit as st
from text_content import IllustratorForm
from .base_structure import DataStructureBase, Field


class Illustrator(DataStructureBase):

    # Simplified to a single ``name`` field, mirroring Publisher (#156). The old
    # forename/surname/gender form added friction for little research value, so the
    # illustrator is now captured as a plain name (and pre-filled from the photo
    # extraction like the publisher). Legacy documents that still carry
    # forename/surname are read tolerantly by ``utilities.author_entry_to_name``.
    fields = {
        'is_registered': False,
        'name': "",
        'entered_by': None,
        'datetime_created': -1,
        'last_updated': -1
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name',
    }

    ref_fields = []

    def __init__(self, db_object=None):
        super().__init__(collection='illustrators', db_object=db_object)

    @property
    def document_id(self):
        return self.name.lower().replace(" ", "_")

    def to_form(self):

        st.header(IllustratorForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one illustrator's values bleeding into the next (#80).
        key_suffix = self.document_id

        self.name = st.text_input(
            IllustratorForm.name_label, value=self.name,
            key=f"illustrator_form_name_{key_suffix}"
        ).strip()

        submitted = st.form_submit_button(
            IllustratorForm.submit_button, key=f"illustrator_form_submit_{key_suffix}"
        )

        if submitted:
            if not self.name.strip():
                st.warning(IllustratorForm.name_required)
                return
            if st.session_state.firestore.document_exists(
                collection='illustrators',
                doc_id=self.document_id
            ):
                st.warning(IllustratorForm.illustrator_exists)
            else:
                st.session_state['current_illustrator'] = self
                st.session_state['active_form_to_confirm'] = 'new_illustrator'
                st.switch_page("./pages/confirm_entry.py")
