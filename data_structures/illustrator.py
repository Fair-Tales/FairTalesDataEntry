import streamlit as st
from text_content import IllustratorForm
from .base_structure import DataStructureBase, Field


class Illustrator(DataStructureBase):

    fields = {
        'is_registered': False,
        'forename': "",
        'surname': "",
        'gender': "",
        'entered_by': None,
        'datetime_created': -1,
        'last_updated': -1
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'forename': 'First name(s)',
        'surname': 'Surname',
        'gender': 'Gender'
    }

    ref_fields = []

    def __init__(self, db_object=None):
        super().__init__(collection='illustrators', db_object=db_object)

    @property
    def name(self):
        return ' '.join([self.forename, self.surname])

    @property
    def document_id(self):
        return self.name.lower().replace(" ", "_")

    def to_form(self):
        from utilities import lookup_person_details, get_anthropic_client

        st.header(IllustratorForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one illustrator's values bleeding into the next (#80).
        key_suffix = self.document_id

        self.forename = st.text_input(
            IllustratorForm.forename_label, value=self.forename,
            key=f"illustrator_form_forename_{key_suffix}"
        ).strip()
        self.surname = st.text_input(
            IllustratorForm.surname_label, value=self.surname,
            key=f"illustrator_form_surname_{key_suffix}"
        ).strip()

        # Apply any suggestion stored by a previous "Look up" click. The lookup
        # is gender-only now that illustrator date of birth has been dropped (#149).
        _suggestion = st.session_state.pop('_illustrator_lookup_suggestion', None)
        if _suggestion:
            if _suggestion.get('gender'):
                self.gender = _suggestion['gender']
        # Feedback from a previous "Look up" click that didn't produce a result.
        if st.session_state.pop('_illustrator_lookup_failed', None):
            st.warning(IllustratorForm.lookup_failed)
        if st.session_state.pop('_illustrator_lookup_no_name', None):
            st.warning(IllustratorForm.lookup_no_name)

        st.write(IllustratorForm.gender_prompt)
        gender_index = 0
        if self.gender in IllustratorForm.gender_options:
            gender_index = IllustratorForm.gender_options.index(self.gender)
        self.gender = st.selectbox(
            IllustratorForm.gender_label,
            options=IllustratorForm.gender_options,
            index=gender_index,
            key=f"illustrator_form_gender_{key_suffix}"
        )

        submitted = st.form_submit_button(
            IllustratorForm.submit_button, key=f"illustrator_form_submit_{key_suffix}"
        )
        ai_available = 'ANTHROPIC_API_KEY' in st.secrets
        lookup_clicked = st.form_submit_button(
            IllustratorForm.lookup_button,
            disabled=not ai_available,
            help=IllustratorForm.lookup_help,
            key=f"illustrator_form_lookup_{key_suffix}"
        )

        if lookup_clicked:
            if self.forename.strip() or self.surname.strip():
                ai_client = get_anthropic_client()
                # Pass the current book title (when one is in progress) as
                # disambiguating context for common names (#113).
                _current_book = st.session_state.get('current_book')
                _book_title = getattr(_current_book, 'title', None)
                with st.spinner(IllustratorForm.lookup_spinner):
                    suggestion = lookup_person_details(
                        self.name.strip(), 'illustrator', ai_client,
                        book_title=_book_title,
                    )
                if suggestion:
                    st.session_state['_illustrator_lookup_suggestion'] = suggestion
                else:
                    st.session_state['_illustrator_lookup_failed'] = True
            else:
                st.session_state['_illustrator_lookup_no_name'] = True
            st.rerun()

        if submitted:
            if not self.forename.strip() or not self.surname.strip():
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
