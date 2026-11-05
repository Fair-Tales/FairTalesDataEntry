import streamlit as st
from datetime import datetime, timezone
from text_content import CharacterForm
from text_content.character_identity import CharacterIdentityText
from utilities import load_character_dict, clear_entity_form_state
from google.api_core.exceptions import FailedPrecondition
from .base_structure import DataStructureBase, Field


class Character(DataStructureBase):

    fields = {
        'is_registered': False,
        'book': None,
        'name': "",
        'storage_id': "",
        'entity_id': "",
        'entity_kind': "unclassified",
        'identity_evidence': [],
        'member_of': [],
        'former_ids': [],
        'unresolved_identity_alternatives': [],
        'name_is_label': False,
        'mentions': [],
        'editorial_labels': [],
        'raw_alias_entries': [],
        'detection_audit': [],
        'unresolved_mentions': [],
        'name_history': [],
        'entry_status': "unverified",
        'gender': "",
        'ethnicity': "",
        'disability': "",
        'protagonist': False,
        'human': True,
        'plural': False,
        'datetime_created': -1,
        'last_updated': -1,
        'entered_by': None
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name',
        'gender': 'Gender',
        'ethnicity': 'Ethnicity',
        'disability': 'Disability',
        'protagonist': 'Is protagonist',
        'human': 'Is human',
        'plural': 'Is plural',
    }

    ref_fields = ['book']

    def __init__(self, db_object=None, book=None):
        super().__init__(collection='characters', db_object=db_object)
        if db_object is None:
            self.book = book
        else:
            stored_id = self.storage_id or self._document_id_for(self.name)
            # Derive legacy identity without writes or a mass migration.
            self._adopt_fields({'storage_id': stored_id,
                                'entity_id': self.entity_id or f'characters/{stored_id}'})

    @classmethod
    def from_snapshot(cls, snapshot):
        payload = snapshot.to_dict()
        if payload is None:
            raise ValueError(CharacterIdentityText.absent_snapshot)
        return cls(db_object={**payload, 'storage_id': snapshot.id,
                              '_update_time': getattr(snapshot, 'update_time', None),
                              'is_registered': True})

    def _prepare_registration(self):
        stored_id = self.storage_id or self.document_id
        self._adopt_fields({'storage_id': stored_id,
                            'entity_id': self.entity_id or f'characters/{stored_id}'})

    def register(self, *, create_only=True):
        if not self.is_registered:
            self._prepare_registration()
        return super().register(create_only=True)

    def register_batched(self, batch, *, create_only=True):
        if not self.is_registered:
            self._prepare_registration()
        return super().register_batched(batch, create_only=True)

    def _assign_field_bundle(self, field, value):
        if not self.__dict__.get('is_registered', False):
            return False
        if field in ('storage_id', 'entity_id') and value != getattr(self, field):
            raise ValueError(CharacterIdentityText.immutable_identity)
        if field == 'name' and value != self.name:
            self.rename(value)
            return True
        return False

    def _validate_bundle_update(self, updates):
        """The shared conditional writer must enforce the descriptor invariant."""
        for field in ('storage_id', 'entity_id'):
            if field in updates and updates.get(field) != getattr(self, field):
                raise ValueError(CharacterIdentityText.immutable_identity)

    def _document_id_for(self, name):
        """Legacy/new-entry ID formula; registered identities never rename.

        Reference IDs are available locally, so computing this value requires no
        document reads. Legacy string and missing references remain readable.
        """
        book_id = getattr(self.book, 'id', None)
        if book_id is None and isinstance(self.book, str):
            book_id = self.book.rsplit('/', 1)[-1]
        return f"{book_id or ''}_{name.replace(' ', '_').lower()}"

    @property
    def document_id(self):
        return self.storage_id or self._document_id_for(self.name)

    def _render_fields(self, key_suffix):
        """Render the character form widgets and return the typed name.

        The non-identifying fields (gender/ethnicity/disability/protagonist/
        human/plural) are assigned to ``self`` so the write-through ``Field``
        descriptors persist edits immediately for a registered character. The
        **name** is returned as a local so it is changed only on submit. A
        registered rename retains its stable identity and archives the old name.
        """
        name = st.text_input(
            CharacterForm.name_label, value=self.name,
            key=f"character_form_name_{key_suffix}"
        )

        gender_index = 0
        if self.gender in CharacterForm.gender_options:
            gender_index = CharacterForm.gender_options.index(self.gender)
        self.gender = st.selectbox(
            CharacterForm.gender_label,
            options=CharacterForm.gender_options,
            index=gender_index,
            help=CharacterForm.gender_help,
            key=f"character_form_gender_{key_suffix}"
        )

        ethnicity_index = 0
        if self.ethnicity in CharacterForm.ethnicity_options:
            ethnicity_index = CharacterForm.ethnicity_options.index(self.ethnicity)
        self.ethnicity = st.selectbox(
            CharacterForm.ethnicity_label,
            options=CharacterForm.ethnicity_options,
            index=ethnicity_index,
            help=CharacterForm.ethnicity_help,
            key=f"character_form_ethnicity_{key_suffix}"
        )

        disability_index = 0
        if self.disability in CharacterForm.disability_options:
            disability_index = CharacterForm.disability_options.index(self.disability)
        self.disability = st.selectbox(
            CharacterForm.disability_label,
            options=CharacterForm.disability_options,
            index=disability_index,
            help=CharacterForm.disability_help,
            key=f"character_form_disability_{key_suffix}"
        )

        self.protagonist = st.checkbox(
            CharacterForm.protagonist_label,
            value=self.protagonist,
            help=CharacterForm.protagonist_help,
            key=f"character_form_protagonist_{key_suffix}"
        )
        self.human = st.checkbox(
            CharacterForm.human_label,
            value=self.human,
            help=CharacterForm.human_help,
            key=f"character_form_human_{key_suffix}"
        )
        self.plural = st.checkbox(
            CharacterForm.plural_label,
            value=self.plural,
            help=CharacterForm.plural_help,
            key=f"character_form_plural_{key_suffix}"
        )

        return name

    def to_form(self):

        st.header(CharacterForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one character's values bleeding into the next (#80).
        key_suffix = self.document_id

        name = self._render_fields(key_suffix)

        submitted = st.form_submit_button(
            CharacterForm.save_button, key=f"character_form_submit_{key_suffix}"
        )

        if submitted:
            # This is a brand-new (unregistered) character, so the name assign
            # does not write through; it just sets the document id used below.
            self.name = name
            if st.session_state.firestore.document_exists(
                collection='characters',
                doc_id=self.document_id
            ):
                # Same-name add EDITS the existing character (#201). After the
                # AI review has created the book's cast, re-entering a detected
                # name used to show only a quiet warning — the Save button
                # looked dead. document_id is book-scoped ({book_id}_{name}),
                # so the collision is always THIS book's character: route to
                # the manage view with its edit form open, with an explanation.
                # (Buttons are not allowed inside an st.form, so this is a
                # direct route rather than an offered choice.)
                clear_entity_form_state("character_form_")
                st.session_state['_editing_character_id'] = self.document_id
                st.session_state['_manage_flash'] = (
                    CharacterForm.character_exists_editing.format(name=self.name)
                )
                st.session_state['now_entering'] = 'manage'
                st.rerun()
            else:
                self.register()
                character_ref = self.get_ref()
                # Link the character to the book currently being edited so the
                # book document holds the list of its characters.
                current_book = st.session_state.get('current_book')
                if current_book is not None:
                    current_book.add_character(character_ref)
                st.session_state['character_dict'][self.name] = character_ref
                # Keep the book-scoped lookup (used for alias entry) in sync.
                st.session_state.setdefault('book_character_dict', {})[self.name] = (
                    character_ref
                )
                # Invalidate shared cache so new/other sessions re-read this
                # character (this session sees it via the in-place update above).
                load_character_dict.clear()
                st.session_state['now_entering'] = 'text'
                st.rerun()

    def edit_form(self):
        """Render an edit form for an existing (registered) character.

        Re-uses the same widgets as ``to_form``. Edits to the non-name fields
        write through to Firestore immediately via the ``Field`` descriptors.
        On submit, a changed name triggers a lossless name update (see
        ``rename``); an unchanged name needs no further work because the other
        fields have already been persisted by the write-through.
        """
        st.header(CharacterForm.edit_header)

        key_suffix = self.document_id

        new_name = self._render_fields(key_suffix)

        submitted = st.form_submit_button(
            CharacterForm.update_button, key=f"character_edit_submit_{key_suffix}"
        )

        if submitted:
            new_name = new_name.strip()
            if not new_name:
                st.warning(CharacterForm.name_required)
                return
            if new_name == self.name:
                # Non-name fields are already saved by write-through; nothing
                # more to do but close the edit form.
                pass
            elif self._document_id_for(new_name) == self.document_id:
                # The descriptor archives the prior form even for case edits.
                self.name = new_name
            elif st.session_state.firestore.document_exists(
                collection='characters',
                doc_id=self._document_id_for(new_name)
            ):
                st.warning(CharacterForm.rename_exists)
                return
            else:
                # Keep the stable reference and archive the prior display name.
                self.rename(new_name)
            st.session_state.pop('_editing_character_id', None)
            st.session_state['now_entering'] = 'manage'
            st.rerun()

    def rename(self, new_name):
        """Retain storage/analytical identity and archive every prior display name.

        A rename no longer recreates/deletes the character or rewrites alias
        attachments. The immutable stored reference remains valid for all callers.
        """
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError(CharacterIdentityText.nonempty_name)
        if new_name == self.name:
            return
        ref = self.get_ref()
        snapshot = ref.get()
        current = snapshot.to_dict() or {}
        if current.get('name') != self.name:
            raise FailedPrecondition(CharacterIdentityText.concurrent_change)
        history = current.get('name_history')
        if history is None:
            history = []
        if not isinstance(history, list):
            raise ValueError(CharacterIdentityText.invalid_history)
        old_name = self.name
        changes = {'name': new_name, 'name_history': [*history, old_name],
                   'storage_id': self.document_id, 'entity_id': self.entity_id,
                   'last_updated': datetime.now(timezone.utc)}
        self.update_fields_conditionally(changes, expected_update_time=snapshot.update_time)
        book_dict = st.session_state.get('book_character_dict', {})
        book_dict.pop(old_name, None)
        book_dict[new_name] = ref
        global_dict = st.session_state.get('character_dict', {})
        previous = global_dict.get(old_name)
        if previous is not None and previous.path == ref.path:
            global_dict.pop(old_name, None)
        global_dict[new_name] = ref
        load_character_dict.clear()

    def delete(self):
        """Retire this character and its attachments, retaining the archive."""
        firestore = st.session_state['firestore']
        character_ref = self.get_ref()

        for alias_doc in firestore.query_stream(
            collection='aliases', field='character', op='==', value=character_ref
        ):
            from .alias import Alias
            Alias.from_snapshot(alias_doc).delete()

        current_book = st.session_state.get('current_book')
        if current_book is not None:
            current_book.remove_character(character_ref)

        st.session_state.get('book_character_dict', {}).pop(self.name, None)
        # Only drop the global entry if it still points at this character — a
        # different book may hold a character with the same name.
        global_dict = st.session_state.get('character_dict', {})
        existing = global_dict.get(self.name)
        if existing is not None and existing.path == character_ref.path:
            global_dict.pop(self.name, None)

        snapshot = character_ref.get()
        self.update_fields_conditionally(
            {'entry_status': 'rejected'}, expected_update_time=snapshot.update_time)
        load_character_dict.clear()
