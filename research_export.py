"""Read-only research projections. Policy: planning/research_export_rules_2026-11-16.md."""

from bibliography import export_publication_columns


def _identity(value):
    """Compare stored user/entity references without reading their documents."""
    return getattr(value, 'path', value)


def _is_import(record):
    return (record.get('source') == 'original_study_import' or
            _identity(record.get('entered_by')) in ('pilot_import', 'users/pilot_import'))


def _is_person(value):
    return bool(value) and _identity(value) not in (
        'pilot_import', 'users/pilot_import', 'unknown', 'databot', 'users/databot')


class ResearchExport:
    """Project raw dictionaries; neither mutate records nor use entity setters."""

    def __init__(self, edit_log=()):
        self.edits = {}
        for edit in edit_log:
            key = (edit.get('entity_type'), edit.get('entity_id'), edit.get('field'))
            previous = self.edits.get(key)
            if previous is None or str(edit.get('timestamp') or '') > str(previous.get('timestamp') or ''):
                self.edits[key] = edit

    def _human_set(self, collection, document_id, record, field):
        origin = (record.get('value_provenance') or {}).get(field) or {}
        if origin.get('source') == 'human':
            return True
        # An explicit current machine origin supersedes historical human edits.
        if origin.get('source') not in (None, 'unknown'):
            return False
        edit = self.edits.get((collection.removesuffix('s'), document_id, field))
        if not edit or not _is_person(edit.get('edited_by')) or 'new_value' not in edit:
            return False
        old, current = _identity(edit['new_value']), _identity(record.get(field))
        return type(old) is type(current) and old == current

    def record(self, collection, document_id, record):
        result = dict(record)
        imported = _is_import(record)
        if collection in ('books', 'characters', 'illustrators', 'publishers'):
            result['imported'] = imported
        if collection == 'characters' and imported:
            for field in ('ethnicity', 'disability', 'plural'):
                human = self._human_set(collection, document_id, record, field)
                result[field] = record.get(field) if human else None
                result[field + '_source'] = 'human' if human else 'missing'
        if collection == 'books':
            result.update(export_publication_columns(record))
            person_validated = record.get('validated') is True and _is_person(record.get('validated_by'))
            result['validated_by_person'] = person_validated
            if imported:
                result['source'] = 'original_study_import'
                result['validated'] = person_validated
                if not person_validated:
                    result['validated_by'] = None
                for field in ('illustrator', 'publisher'):
                    source = ('missing' if record.get(field) is None else
                              'human' if self._human_set(collection, document_id, record, field)
                              else 'automated_lookup')
                    result[field + '_source'] = source
                # The bibliography adapter keeps the old publisher separately;
                # a scoped publisher has its own publication evidence.
                result['legacy_publisher_source'] = result['publisher_source']
                result['publisher_source'] = ('missing' if result.get('publisher') is None
                                              else 'publication_evidence')
        if collection in ('illustrators', 'publishers') and imported:
            result['name_source'] = ('missing' if record.get('name') is None else
                                     'human' if self._human_set(collection, document_id, record, 'name')
                                     else 'automated_lookup')
        return result
