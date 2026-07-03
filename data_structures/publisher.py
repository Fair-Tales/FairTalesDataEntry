import streamlit as st
from text_content import PublisherForm
from .base_structure import DataStructureBase, Field


class Publisher(DataStructureBase):

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
        super().__init__(collection='publishers', db_object=db_object)

    #@property
    #def name(self):
    #    return ' '.join([self.forename, self.surname])

    @property
    def document_id(self):
        return self.name.lower().replace(" ", "_")

    def to_form(self):

        st.header(PublisherForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one publisher's values bleeding into the next (#80).
        key_suffix = self.document_id

        self.name = st.text_input(
            PublisherForm.name_label, value=self.name,
            key=f"publisher_form_name_{key_suffix}"
        ).strip()

        submitted = st.form_submit_button(
            PublisherForm.submit_button, key=f"publisher_form_submit_{key_suffix}"
        )

        if submitted:
            if not self.name.strip():
                st.warning(PublisherForm.name_required)
                return
            if st.session_state.firestore.document_exists(
                collection='publishers',
                doc_id=self.document_id
            ):
                st.warning(PublisherForm.publisher_exists)
            else:
                st.session_state['current_publisher'] = self
                st.session_state['active_form_to_confirm'] = 'new_publisher'
                st.switch_page("./pages/confirm_entry.py")
