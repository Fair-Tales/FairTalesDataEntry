"""Lossless recorded aliases with a separate, overridable lexical projection."""
from datetime import datetime, timezone

import streamlit as st
from google.api_core.exceptions import AlreadyExists, FailedPrecondition
from google.cloud.firestore_v1 import LastUpdateOption

from text_content import AliasForm
from alias_schema import (
    ALIAS_SCHEMA_VERSION, alias_storage_id, canonical_bundle, effective_canonical,
    raw_surface_hash,
)
from .base_structure import DataStructureBase, Field

_UNCHANGED_OVERRIDE = object()


class Alias(DataStructureBase):
    fields = {
        'is_registered': False, 'character': None, 'book': None, 'name': "",
        'canonical_form': "", 'canonical_source': "", 'canonical_override': False,
        'canonical_rule_version': "", 'canonical_input_hash': "",
        'alias_schema_version': ALIAS_SCHEMA_VERSION, 'storage_id': "",
        'source': "legacy_unverified", 'provenance': [],
        'entry_status': "unverified", 'supersedes': None, 'superseded_by': None,
        'effective_character': None, 'raw_history': [], 'canonical_history': [],
        'datetime_created': -1, 'last_updated': -1, 'entered_by': None,
    }
    for field in fields:
        if field not in DataStructureBase.base_class_fields + ['is_registered']:
            vars()[field] = Field()

    form_fields = {'name': AliasForm.alias_label, 'canonical_form': AliasForm.canonical_label}
    ref_fields = ['character', 'book', 'effective_character']

    def __init__(self, db_object=None, book=None):
        super().__init__(collection='aliases', db_object=db_object)
        if db_object is None:
            self.book = book
        elif not self.storage_id:
            book_id = getattr(self.book, 'id', self.book.rsplit('/', 1)[-1] if isinstance(self.book, str) else '')
            self._adopt_fields({'storage_id': f"{book_id}_{self.name.replace(' ', '_').lower()}"})

    @classmethod
    def from_snapshot(cls, snapshot):
        """The actual stored ID is authoritative, including legacy duplicate rows."""
        payload = snapshot.to_dict()
        if payload is None:
            raise ValueError("Cannot load an absent alias snapshot")
        return cls(db_object={**payload, 'storage_id': snapshot.id,
                              '_update_time': getattr(snapshot, 'update_time', None),
                              'is_registered': True})

    @property
    def document_id(self):
        if self.storage_id:
            return self.storage_id
        if self.character is not None and self.name and not self.is_registered:
            return alias_storage_id(self.book, self.character, self.name)
        # Compatibility for old callers that cannot provide a snapshot ID.
        return f"{getattr(self.book, 'id', '')}_{self.name.replace(' ', '_').lower()}"

    def effective_canonical(self):
        return effective_canonical(self.to_dict())

    def _validate_bundle_update(self, updates):
        canonical_keys = set(canonical_bundle(self.name)) - {'alias_schema_version'}
        for field in ('book', 'character'):
            if field in updates and updates[field] != getattr(self, field):
                raise ValueError("Use a linked correction to change a recorded attachment")
        if 'name' in updates and updates['name'] != self.name:
            history = updates.get('raw_history')
            if (not canonical_keys.issubset(updates) or not isinstance(history, list)
                    or history != [*self.raw_history, self.name]):
                raise ValueError("Raw edits require the canonical bundle and retained surface history")
        if ('raw_history' in updates and
                (not isinstance(updates['raw_history'], list) or
                 updates['raw_history'][:len(self.raw_history)] != self.raw_history)):
            raise ValueError("Recorded raw history cannot be overwritten")
        if 'canonical_history' in updates:
            history = updates['canonical_history']
            if (not isinstance(history, list) or
                    history[:len(self.canonical_history)] != self.canonical_history):
                raise ValueError("Recorded canonical history cannot be overwritten")
        if canonical_keys.intersection(updates):
            merged = {**self.to_dict(), **updates}
            if effective_canonical(merged)['status'] != 'valid':
                raise ValueError("Canonical fields must form a valid atomic bundle")
            if self.canonical_override and (
                    merged['canonical_form'] != self.canonical_form or not merged['canonical_override']):
                history = updates.get('canonical_history', [])
                if (len(history) <= len(self.canonical_history) or
                        history[-1].get('canonical_form') != self.canonical_form):
                    raise ValueError("Changing a manual canonical form requires retained override history")
        if 'storage_id' in updates and updates['storage_id'] != self.document_id:
            raise ValueError("Alias storage_id is immutable")

    def _expected_update_time(self):
        if self._update_time is None:
            # Legacy callers without snapshot metadata get one guarded refresh.
            snapshot = self.get_ref().get()
            current = snapshot.to_dict() or {}
            compared = ('name', 'canonical_form', 'canonical_source', 'canonical_override',
                        'canonical_rule_version', 'canonical_input_hash', 'entry_status',
                        'canonical_history', 'raw_history')
            if any(current.get(key, self.fields.get(key)) != getattr(self, key)
                   for key in compared):
                raise FailedPrecondition("Alias changed; reload before saving")
            self._update_time = snapshot.update_time
        return self._update_time

    def _assign_field_bundle(self, field, value):
        """Keep descriptor writes atomic, including raw edits and manual overrides."""
        if not self.__dict__.get('is_registered', False):
            return False
        if field == 'storage_id' and value != self.storage_id:
            raise ValueError("Alias storage_id is immutable")
        if field in ('book', 'character') and value != getattr(self, field):
            raise ValueError("Use a linked correction to change a recorded attachment")
        if field == 'canonical_form':
            self.set_canonical_override(value)
            return True
        if field == 'name' and value != self.name:
            manual = self.canonical_form if self.canonical_override else None
            changes = {'name': value, 'raw_history': [*self.raw_history, self.name],
                       **canonical_bundle(value, manual)}
            self.update_fields_conditionally(
                changes, expected_update_time=self._expected_update_time(),
                expected_raw_hash=raw_surface_hash(self.name))
            return True
        if field in canonical_bundle(self.name):
            raise ValueError("Use set_canonical_override to update the canonical bundle")
        if value != getattr(self, field):
            self.update_fields_conditionally({field: value},
                                             expected_update_time=self._expected_update_time())
        return True

    def set_canonical_override(self, value=None):
        """An empty value explicitly resets to derived; raw text stays intact."""
        bundle = canonical_bundle(self.name, value)
        previous = {key: getattr(self, key) for key in bundle}
        if previous == bundle:
            return
        if self.canonical_form or self.canonical_override:
            bundle['canonical_history'] = [*self.canonical_history, {
                **previous, 'changed_at': datetime.now(timezone.utc)}]
        if self.is_registered:
            self.update_fields_conditionally(
                bundle, expected_update_time=self._expected_update_time(),
                expected_raw_hash=raw_surface_hash(self.name))
        else:
            self._adopt_fields(bundle)

    def _prepare_registration(self):
        override = self.canonical_form if self.canonical_override else None
        self._adopt_fields(canonical_bundle(self.name, override))
        if not self.storage_id:
            self.storage_id = self.document_id

    def register(self, *, create_only=True):
        if not self.is_registered:
            self._prepare_registration()
        # Aliases always use create semantics: a hash collision is never a merge.
        try:
            return super().register(create_only=True)
        except AlreadyExists:
            self.check_collision_payload(self.get_ref().get())
            raise

    def check_collision_payload(self, snapshot):
        """An existing hash can be reused only for the same complete raw tuple."""
        payload = snapshot.to_dict() or {}
        for field in ('book', 'character', 'name'):
            expected, actual = getattr(self, field), payload.get(field)
            if getattr(expected, 'path', expected) != getattr(actual, 'path', actual):
                raise ValueError("Alias ID collision with a different recorded tuple")

    def register_batched(self, batch, *, create_only=True):
        if not self.is_registered:
            self._prepare_registration()
        return super().register_batched(batch, create_only=True)

    def create_correction(self, surface, *, character=None, canonical_override=_UNCHANGED_OVERRIDE):
        """Atomically retain an original and add its linked corrective entry."""
        if not surface.strip():
            raise ValueError("A correction requires a nonempty surface")
        new_alias = Alias(book=self.book)
        new_alias.name = surface
        new_alias.character = self.character if character is None else character
        new_alias.source = 'human_correction'
        new_alias.provenance = [*self.provenance, {'supersedes': self.document_id}]
        new_alias.supersedes = self.document_id
        new_alias.entry_status = 'accepted'
        if canonical_override is _UNCHANGED_OVERRIDE:
            canonical_override = self.canonical_form if self.canonical_override else None
        new_alias.set_canonical_override(canonical_override)
        expected_time = self._expected_update_time()
        batch = st.session_state.firestore.write_batch()
        acknowledge = new_alias.register_batched(batch)
        changes = {'entry_status': 'superseded', 'superseded_by': new_alias.document_id}
        batch.update(self.get_ref(), changes, option=LastUpdateOption(expected_time))
        batch.commit()
        acknowledge()
        self._adopt_fields(changes)
        self._update_time = None
        return new_alias

    def to_form(self):
        key_suffix = self.document_id
        st.header(AliasForm.header)
        book_character_dict = st.session_state.get('book_character_dict', {})
        character_options = list(book_character_dict)
        if not character_options:
            st.warning(AliasForm.no_characters)
            return
        current_path = getattr(self.character, 'path', None)
        current_name = next((name for name, ref in book_character_dict.items()
                             if getattr(ref, 'path', None) == current_path), None)
        orphaned = current_name is None and (self.is_registered or self.character is not None)
        if orphaned:
            st.warning(AliasForm.missing_character)
        character_index = (character_options.index(current_name)
                           if current_name in character_options else None if orphaned else 0)
        selected_character = st.selectbox(
            AliasForm.select_character_label, options=character_options,
            index=character_index, key=f"alias_form_character_{key_suffix}")
        surface = st.text_input(AliasForm.alias_label, value=self.name,
                                key=f"alias_form_name_{key_suffix}")
        override = st.text_input(
            AliasForm.canonical_override_label,
            value=self.canonical_form if self.canonical_override else '',
            help=AliasForm.canonical_override_help,
            key=f"alias_form_canonical_override_{key_suffix}")
        submitted = st.form_submit_button(
            AliasForm.save_button, key=f"alias_form_submit_{key_suffix}")
        if not submitted:
            return
        if not surface.strip():
            st.warning(AliasForm.name_required)
            return
        selected_ref = book_character_dict.get(selected_character)
        if selected_ref is None:
            st.warning(AliasForm.missing_character)
            return
        self.name = surface
        self.character = selected_ref
        self.source = 'human_entry'
        self.set_canonical_override(override)
        try:
            self.register()
        except AlreadyExists:
            st.warning(AliasForm.character_exists)
            return
        st.session_state['now_entering'] = 'text'
        st.session_state.pop('current_alias', None)
        st.rerun()

    def delete(self):
        """Retire the attachment while preserving the recorded student surface."""
        self.update_fields_conditionally(
            {'entry_status': 'rejected'}, expected_update_time=self._expected_update_time())
