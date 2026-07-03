import streamlit as st
from text_content import AuthorForm
from .base_structure import DataStructureBase, Field


class Author(DataStructureBase):

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
        super().__init__(collection='authors', db_object=db_object)

    @property
    def name(self):
        return ' '.join([self.forename, self.surname])

    @property
    def document_id(self):
        return self.name.lower().replace(" ", "_")

    def to_form(self):
        from utilities import lookup_person_details, get_anthropic_client

        st.header(AuthorForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one author's values bleeding into the next (#80).
        key_suffix = self.document_id

        self.forename = st.text_input(
            AuthorForm.forename_label, value=self.forename,
            key=f"author_form_forename_{key_suffix}"
        ).strip()
        self.surname = st.text_input(
            AuthorForm.surname_label, value=self.surname,
            key=f"author_form_surname_{key_suffix}"
        ).strip()

        # Apply any suggestion stored by a previous "Look up" click. The lookup
        # is gender-only now that author date of birth has been dropped (#149).
        _suggestion = st.session_state.pop('_author_lookup_suggestion', None)
        if _suggestion:
            if _suggestion.get('gender'):
                self.gender = _suggestion['gender']
        # Feedback from a previous "Look up" click that didn't produce a result.
        if st.session_state.pop('_author_lookup_failed', None):
            st.warning(AuthorForm.lookup_failed)
        if st.session_state.pop('_author_lookup_no_name', None):
            st.warning(AuthorForm.lookup_no_name)

        st.write(AuthorForm.gender_prompt)
        gender_index = 0
        if self.gender in AuthorForm.gender_options:
            gender_index = AuthorForm.gender_options.index(self.gender)
        self.gender = st.selectbox(
            AuthorForm.gender_label,
            options=AuthorForm.gender_options,
            index=gender_index,
            key=f"author_form_gender_{key_suffix}"
        )

        submitted = st.form_submit_button(
            AuthorForm.submit_button, key=f"author_form_submit_{key_suffix}"
        )
        ai_available = 'ANTHROPIC_API_KEY' in st.secrets
        lookup_clicked = st.form_submit_button(
            AuthorForm.lookup_button,
            disabled=not ai_available,
            help=AuthorForm.lookup_help,
            key=f"author_form_lookup_{key_suffix}"
        )

        if lookup_clicked:
            if self.forename.strip() or self.surname.strip():
                ai_client = get_anthropic_client()
                # Pass the current book title (when one is in progress) as
                # disambiguating context for common names (#113).
                _current_book = st.session_state.get('current_book')
                _book_title = getattr(_current_book, 'title', None)
                with st.spinner(AuthorForm.lookup_spinner):
                    suggestion = lookup_person_details(
                        self.name.strip(), 'author', ai_client,
                        book_title=_book_title,
                    )
                if suggestion:
                    st.session_state['_author_lookup_suggestion'] = suggestion
                else:
                    st.session_state['_author_lookup_failed'] = True
            else:
                st.session_state['_author_lookup_no_name'] = True
            st.rerun()

        if submitted:
            if not self.forename.strip() or not self.surname.strip():
                st.warning(AuthorForm.name_required)
                return
            if st.session_state.firestore.document_exists(
                collection='authors',
                doc_id=self.document_id
            ):
                st.warning(AuthorForm.author_exists)
            else:
                st.session_state['current_author'] = self
                st.session_state['active_form_to_confirm'] = 'new_author'
                st.switch_page("./pages/confirm_entry.py")
