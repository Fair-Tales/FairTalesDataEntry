"""C3 research export: synthetic records only, including the actual ZIP path."""
import ast
import copy
import csv
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

from google.api_core.exceptions import GoogleAPIError, ServiceUnavailable
import pytest

from research_export import ResearchExport
from text_content import Admin


def character(**changes):
    return dict(entered_by='pilot_import', name='Fox', ethnicity='Not specified',
                disability='Not specified', plural=False, **changes)


def edit(value, **changes):
    return dict(entity_type='character', entity_id='fox', field='plural',
                new_value=value, edited_by='users/researcher', timestamp='2026-11-16T10:00:00Z',
                **changes)


@pytest.mark.parametrize('marker', [
    {'entered_by': 'pilot_import'}, {'entered_by': 'users/pilot_import'},
    {'entered_by': SimpleNamespace(path='users/pilot_import', id='pilot_import')},
    {'source': 'original_study_import'},
])
def test_import_defaults_missing_without_mutating_source(marker):
    raw = {**character(), 'entered_by': None, **marker}
    before = copy.deepcopy(raw)
    result = ResearchExport().record('characters', 'fox', raw)
    assert result['imported'] is True
    for field in ('ethnicity', 'disability', 'plural'):
        assert result[field] is None
        assert result[field + '_source'] == 'missing'
    assert raw == before


@pytest.mark.parametrize('value', [True, False])
@pytest.mark.parametrize('evidence', ['provenance', 'edit_log'])
def test_person_sets_plural_preserving_true_and_false(value, evidence):
    raw = character()
    raw['plural'] = value
    logs = []
    if evidence == 'provenance':
        raw['value_provenance'] = {'plural': {'source': 'human', 'account': 'researcher'}}
    else:
        logs = [edit(value)]
    result = ResearchExport(logs).record('characters', 'fox', raw)
    assert result['plural'] is value
    assert result['plural_source'] == 'human'
    assert result['ethnicity'] is result['disability'] is None


def test_explicit_human_not_specified_is_a_code_and_other_fields_stay_missing():
    raw = character(value_provenance={'ethnicity': {'source': 'human'}})
    result = ResearchExport().record('characters', 'fox', raw)
    assert result['ethnicity'] == 'Not specified'
    assert result['disability'] is result['plural'] is None


@pytest.mark.parametrize('changes', [
    {'entity_id': 'another'}, {'entity_type': 'book'}, {'field': 'human'},
    {'edited_by': 'pilot_import'}, {'edited_by': None}, {'new_value': True},
    {'new_value': 0},
])
def test_unrelated_or_nonhuman_log_cannot_rescue_default(changes):
    result = ResearchExport([{**edit(False), **changes}]).record('characters', 'fox', character())
    assert result['plural'] is None


def test_stale_log_cannot_override_current_provenance_or_latest_log():
    raw = character(value_provenance={'plural': {'source': 'ai_pipeline'}})
    assert ResearchExport([edit(False)]).record('characters', 'fox', raw)['plural'] is None
    later = {**edit(True), 'timestamp': '2026-11-16T11:00:00Z'}
    for logs in ([edit(False), later], [later, edit(False)]):
        assert ResearchExport(logs).record('characters', 'fox', character())['plural'] is None


def test_nonimported_false_and_null_unchanged():
    raw = {**character(), 'entered_by': 'researcher', 'ethnicity': None}
    result = ResearchExport().record('characters', 'fox', raw)
    assert result['plural'] is False
    assert result['ethnicity'] is None
    assert result['imported'] is False


@pytest.mark.parametrize('validator, expected', [
    ('pilot_import', False), ('users/pilot_import', False), (None, False),
    ('users/researcher', True),
    (SimpleNamespace(path='users/researcher', id='researcher'), True),
])
def test_import_validation_is_not_human_validation(validator, expected):
    raw = dict(entered_by='pilot_import', validated=True, validated_by=validator)
    result = ResearchExport().record('books', 'book', raw)
    assert result['source'] == 'original_study_import'
    assert result['imported'] is True
    assert result['validated'] is result['validated_by_person'] is expected
    assert result['validated_by'] == (validator if expected else None)
    assert raw['validated'] is True


def test_imported_lookup_links_and_shared_names_are_labelled():
    raw = dict(entered_by='pilot_import', illustrator='illustrators/ivy', publisher='publishers/press')
    before = copy.deepcopy(raw)
    result = ResearchExport().record('books', 'book', raw)
    assert result['illustrator_source'] == 'automated_lookup'
    assert result['legacy_publisher'] == raw['publisher']
    assert result['legacy_publisher_source'] == 'automated_lookup'
    assert result['publisher'] is None  # Existing bibliography projection, not a new code.
    assert result['publisher_source'] == 'missing'
    assert raw == before
    for collection in ('illustrators', 'publishers'):
        result = ResearchExport().record(collection, 'lookup', {'entered_by': 'pilot_import', 'name': 'Lookup'})
        assert result['name_source'] == 'automated_lookup'


def test_later_human_illustrator_link_is_preserved():
    raw = dict(entered_by='pilot_import', illustrator='illustrators/ivy')
    log = {**edit('illustrators/ivy'), 'entity_type': 'book', 'entity_id': 'book', 'field': 'illustrator'}
    result = ResearchExport([log]).record('books', 'book', raw)
    assert result['illustrator'] == raw['illustrator']
    assert result['illustrator_source'] == 'human'


def test_scoped_publisher_retains_its_own_evidence_even_if_name_matches_import():
    raw = dict(entered_by='pilot_import', publisher='Press', metadata_schema_version='bibliography-v2',
               photographed_edition={'imprint': 'Press', 'imprint_status': 'evidenced'})
    result = ResearchExport().record('books', 'book', raw)
    assert result['publisher'] == 'Press'
    assert result['publisher_source'] == 'publication_evidence'
    assert result['legacy_publisher_source'] == 'automated_lookup'


def run_download(records, *, fail_log=False):
    def stream(collection):
        if collection == 'edit_log' and fail_log:
            raise ServiceUnavailable('synthetic failure')
        return [SimpleNamespace(id=key, to_dict=lambda row=row: row)
                for key, row in records.get(collection, {}).items()]

    downloads, errors = [], []
    class Stopped(Exception):
        pass

    def stop():
        raise Stopped()

    st = SimpleNamespace(button=lambda *a, **kw: True, download_button=lambda **kw: downloads.append(kw),
                         error=errors.append, stop=stop,
                         session_state=SimpleNamespace(firestore=SimpleNamespace(get_all_documents_stream=stream)))
    module = ast.parse((Path(__file__).resolve().parents[1] / 'pages/admin.py').read_text())
    node = next(node for node in module.body if isinstance(node, ast.If)
                and 'admin_prepare_book_download_button' in ast.unparse(node.test))
    try:
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'pages/admin.py', 'exec'),
             dict(st=st, Admin=Admin, io=io, zipfile=zipfile, csv=csv, GoogleAPIError=GoogleAPIError))
    except Stopped:
        pass
    return downloads, errors


def test_actual_admin_zip_blanks_false_validation_and_lookup_sources():
    records = {
        'characters': {'unknown': character(), 'fox': character(),
                       'retired': character(entry_status='rejected')},
        'edit_log': {'edit': edit(False)},
        'books': {'book': dict(entered_by='pilot_import', validated=True, validated_by='pilot_import',
                               illustrator='illustrators/ivy', publisher='publishers/press')},
        'illustrators': {'ivy': dict(entered_by='pilot_import', name='Ivy')},
    }
    before = copy.deepcopy(records)
    downloads, errors = run_download(records)
    assert not errors
    with zipfile.ZipFile(io.BytesIO(downloads[0]['data'])) as archive:
        def rows(collection):
            return list(csv.DictReader(io.StringIO(archive.read(collection + '.csv').decode())))
        unknown, human = rows('characters')
        assert all(unknown[field] == '' for field in ('ethnicity', 'disability', 'plural'))
        assert human['plural'] == 'False'
        book = rows('books')[0]
        assert book['validated'] == book['validated_by_person'] == 'False'
        assert book['validated_by'] == '' and book['imported'] == 'True'
        assert book['illustrator_source'] == book['legacy_publisher_source'] == 'automated_lookup'
        assert rows('illustrators')[0]['name_source'] == 'automated_lookup'
    assert records == before


def test_no_download_if_edit_history_cannot_be_read():
    downloads, errors = run_download({}, fail_log=True)
    assert downloads == []
    assert len(errors) == 1 and 'edit history' in errors[0]
