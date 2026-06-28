import streamlit as st
from text_content import PublisherForm
from .base_structure import DataStructureBase, Field
from datetime import date


class Publisher(DataStructureBase):

    fields = {
        'is_registered': False,
        'name': "",
        'founding_year': 1970,
        'entered_by': None,
        'datetime_created': -1,
        'last_updated': -1
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name',
        'founding_year': 'Founding year',
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

        self.name = st.text_input(PublisherForm.name_label, value=self.name).strip()

        year_given = int(st.selectbox(
            PublisherForm.founding_year_label,
            options = (x for x in ([-1, -2]+[y for y in range(1900, (date.today().year + 1))])),
            index=0,
            placeholder=PublisherForm.founding_year_placeholder,
            format_func = lambda x: PublisherForm.founding_year_unknown if x == -1 else (PublisherForm.founding_year_earlier if x == -2 else str(x))
        ))

        if year_given > 0:
            self.founding_year = year_given
        else:
            self.founding_year = None

        submitted = st.form_submit_button(PublisherForm.submit_button)

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
