"""Write-through provenance for Page, Character and Alias only.

An absent map entry is unknown. Loading never stamps or migrates a document.
Value and provenance updates share the same write, including guarded edits.
"""
from datetime import datetime, timezone
import streamlit as st

from corpus_provenance import human_origin


class CorpusProvenance:
    def provenance_for(self, field):
        return self.value_provenance.get(field, {'source': 'unknown'})

    def _human_updates(self, changes):
        origin = human_origin(st.session_state.get('username'))
        provenance = dict(self.value_provenance)
        for field in changes:
            if field not in self.provenance_fields:
                continue
            previous = self.provenance_for(field)
            generated = previous if previous.get('source') == 'ai_pipeline' else previous.get('generated_from')
            provenance[field] = {**origin, **({'generated_from': generated} if generated else {})}
        updates = {**changes, 'value_provenance': provenance}
        if self.belongs_to_collection == 'pages' and 'text' in changes:
            updates.update(extraction_status='human_entered', extraction_failure_kind=None)
        return updates

    def _assign_provenanced_field(self, field, value):
        if not self.__dict__.get('is_registered', False) or field not in self.provenance_fields:
            return False
        if value != getattr(self, field):
            self.__dict__[field] = value
            self.update_record(field, value)
        return True

    def update_record(self, field, value):
        if self.is_registered and field in self.provenance_fields:
            updates = self._human_updates({field: value})
            updates['last_updated'] = datetime.now(timezone.utc)
            st.session_state.firestore.update_fields(
                collection=self.belongs_to_collection, document=self.document_id, values=updates)
            self._adopt_fields(updates)
        else:
            super().update_record(field, value)

    def update_fields_conditionally(self, updates, **kwargs):
        if ('value_provenance' not in updates and
                any(key in self.provenance_fields and value != getattr(self, key)
                    for key, value in updates.items())):
            updates = self._human_updates(updates)
        return super().update_fields_conditionally(updates, **kwargs)

    def _prepare_value_provenance(self):
        # New manual entities only. Pipeline/import writers supply their own map.
        if not self.is_registered and not self.value_provenance and self.belongs_to_collection != 'pages':
            fields = tuple(self.form_fields)
            if self.belongs_to_collection == 'aliases':
                fields = ('name', 'character') + (('canonical_form',) if self.canonical_override else ())
            origin = human_origin(st.session_state.get('username'))
            self.value_provenance = {key: dict(origin) for key in fields}
            if self.source in ('unknown', 'legacy_unverified'):
                self.source = 'human_entry'

    def register(self, **kwargs):
        self._prepare_value_provenance()
        return super().register(**kwargs)

    def register_batched(self, batch, **kwargs):
        self._prepare_value_provenance()
        return super().register_batched(batch, **kwargs)
