import streamlit as st
from utilities import author_entry_to_name, page_layout, check_authentication_status, navigate_to
from text_content import CharacterForm, AddCharacterPage

check_authentication_status()

# TODO: add option to edit existing character (change names, aliases etc)
def new_character():

    # first find existing authors:
    author_dict = {
        author_entry_to_name(author): author.id
        for author in
        st.session_state.firestore.get_all_documents_stream(collection='authors')
    }

    with st.form("new_book"):
        st.header(AddCharacterPage.header)

        metadata = {
            key: None for key in ['name', 'alias', 'gender', 'ethnicity', 'disability', 'is_plural', 'is_human']
        }
        metadata['name'] = st.text_input(AddCharacterPage.name_label, key="add_character_name_input")
        metadata['alias'] = st.text_input(AddCharacterPage.alias_label, key="add_character_alias_input")
        metadata['gender'] = st.selectbox(
            AddCharacterPage.gender_label,
            options=AddCharacterPage.gender_options,
            key="add_character_gender_select"
        )
        metadata['ethnicity'] = st.selectbox(
            AddCharacterPage.ethnicity_label,
            options=CharacterForm.ethnicity_options,
            help=CharacterForm.ethnicity_help,
            key="add_character_ethnicity_select"
        )
        metadata['disability'] = st.selectbox(
            AddCharacterPage.disability_label,
            options=CharacterForm.disability_options,
            help=CharacterForm.disability_help,
            key="add_character_disability_select"
        )
        metadata['is_plural'] = st.checkbox(AddCharacterPage.plural_label, key="add_character_plural_checkbox")
        metadata['is_human'] = st.checkbox(AddCharacterPage.human_label, value=True, key="add_character_human_checkbox")

        submitted = st.form_submit_button(AddCharacterPage.submit_button, key="add_character_submit_button")
        if submitted:
            if not metadata['name'].strip():
                st.warning(AddCharacterPage.name_required)
                return
            st.session_state['character_details'] = metadata
            st.session_state['active_form_to_confirm'] = 'new_character'
            navigate_to("./pages/confirm_entry.py")


page_layout(current_page="./pages/add_character.py")

new_character()

cancel_button = st.button(AddCharacterPage.cancel_button, key="add_character_cancel_button")

if cancel_button:
    st.switch_page("./pages/user_home.py")
