"""Event-scoped publication, lossless migration and bounded metadata repair."""
from copy import deepcopy
import json

import pytest

from bibliography import (METADATA_SCHEMA_VERSION, adapt_legacy_metadata,
    export_publication_columns, fallback_page_candidates, legacy_publication,
    publication_projection, reconcile_metadata, valid_isbn)
from scripts.migrate_publication import BookRecord, main, plan_migration, run_migration


def event(publisher='Imprint', year=2000, scope='edition', **kwargs):
    return {'publisher': publisher, 'publisher_role': 'imprint', 'year': year,
            'scope': scope, 'page_number': 2, 'source_page_id': 'book:2',
            'image_hash': 'fixture-image-hash', 'provenance': 'photo',
            'quote': f'{scope} {publisher} {year}', **kwargs}


def test_both_publication_targets_and_house_are_distinct():
    result = publication_projection([event(publishing_house='Parent'), event('Original', 1980, 'original')])
    assert result['metadata_schema_version'] == METADATA_SCHEMA_VERSION
    assert result['photographed_edition']['imprint'] == 'Imprint'
    assert result['photographed_edition']['publishing_house'] == 'Parent'
    assert result['first_publication']['publisher'] == 'Original'
    assert result['published_year'] == 1980
    assert result['published_year_scope'] == 'work_first'
    assert result['publisher'] == 'Imprint'


def test_copyright_year_and_legacy_year_never_become_first_publication():
    old = {'publisher': 'Student publisher', 'published': 1957}
    result = publication_projection([event(year=1900, scope='copyright')], legacy=legacy_publication(old))
    assert result['first_publication']['year'] is None
    assert result['photographed_edition']['year'] is None
    assert result['publication_events'][0]['year'] == 1900
    assert result['legacy_publication']['published_year'] == 1957


def test_original_outranks_territorial_first_without_losing_events():
    result = publication_projection([event(year=1973, scope='territorial_first', territory='UK'),
                                    event(year=1957, scope='original')])
    assert result['first_publication']['year'] == 1957
    assert result['first_publication']['scope'] == 'work_first'
    assert len(result['publication_events']) == 2


def test_territorial_first_keeps_territory_and_rejects_conflicts():
    result = publication_projection([event(year=1973, scope='territorial_first', territory='UK')])
    assert result['first_publication']['territory'] == 'UK'
    conflicting = publication_projection([event(year=1973, scope='territorial_first', territory='UK'),
                                          event(year=1960, scope='territorial_first', territory='US')])
    assert conflicting['first_publication']['year'] is None
    assert conflicting['metadata_conflicts']


def test_never_pair_publisher_and_year_across_incomplete_events():
    result = publication_projection([event(year=None), event(publisher=None, year=2000)])
    assert result['photographed_edition']['publisher'] is None
    assert result['photographed_edition']['year'] is None
    assert result['metadata_conflicts']


def test_explicit_complete_event_can_be_corroborated_by_partial():
    result = publication_projection([event(), event(year=None)])
    assert result['photographed_edition']['year'] == 2000
    assert result['photographed_edition']['publisher'] == 'Imprint'


def test_no_event_evidence_and_wrong_source_remain_unresolved():
    for candidate in (event(quote=''), event(provenance='unresolved_source')):
        assert publication_projection([candidate])['photographed_edition']['publisher'] is None


@pytest.mark.parametrize('role', ['printer', 'rights_holder', 'distributor', 'parent'])
def test_other_organisations_are_not_imprints(role):
    target = publication_projection([event(publisher_role=role)])['photographed_edition']
    assert target['imprint'] is None
    assert target['publishing_house'] == ('Imprint' if role == 'parent' else None)


def test_shared_v2_adapter_and_exports_do_not_promote_legacy_values():
    old = {'publisher': 'Student', 'published': 1999, 'entered_by': 'student', 'last_updated': 'stamp'}
    original = deepcopy(old)
    result = adapt_legacy_metadata(old)
    assert old == original
    assert result['publisher'] is None
    assert result['legacy_publication']['publisher'] == 'Student'
    assert result['legacy_publication']['timestamps']['last_updated'] == 'stamp'
    columns = export_publication_columns(old)
    assert columns['publication_export_version'] == 'bibliography-export-v2'
    assert columns['photographed_edition.imprint'] is None
    assert columns['first_publication.year'] is None
    assert columns['publisher'] is None and columns['published_year'] is None
    assert columns['legacy_publisher'] == 'Student'


def test_reconciliation_requires_exact_full_name_not_surname():
    credits = [{'name': name, 'role': role, 'page_number': 2, 'quote': f'{role} {name}'}
               for name, role in [('Ian Beck', 'reteller'), ('Ian Beck', 'illustrator'),
                                  ('Jane Beck', 'author'), ('Editor', 'editor')]]
    result = reconcile_metadata([{'credits': credits}, {'credits': [credits[0]]}])
    assert result['authors'] == ['Ian Beck', 'Jane Beck']
    assert result['illustrators'] == ['Ian Beck']
    assert len(result['credits']) == 4
    assert result['metadata_conventions']['conventions']['C6']['status'] == 'provisional'


def test_title_evidence_ranking_and_unresolved_tie():
    garbled = {'title': {'value': 'Bby', 'evidence': [{'page_number': 1, 'quote': 'Bby'}]}}
    clear = {'title': {'value': 'Cave Baby', 'evidence': [{'page_number': 2, 'quote': 'Cave Baby'},
                                                       {'page_number': 3, 'quote': 'Cave Baby'}]}}
    result = reconcile_metadata([garbled, clear])
    assert result['title'] == 'Cave Baby'
    assert len(result['title_candidates']) == 2
    tie = {'title': {'value': 'Baby', 'evidence': [{'page_number': 4, 'quote': 'Baby'}]}}
    assert reconcile_metadata([garbled, tie])['title'] is None


def test_credit_conflicts_use_corrobation_preserving_every_original():
    payload = {'credits': [
        {'name': 'Garbled', 'role': 'author', 'page_number': 1, 'quote': 'Garbled'},
        {'name': 'Visible', 'role': 'author', 'page_number': 2, 'quote': 'Written by Visible'},
        {'name': 'Visible', 'role': 'author', 'page_number': 3, 'quote': 'Text by Visible'},
    ], 'conflicts': [{'field': 'authors', 'candidates': [{'value': 'Garbled'}, {'value': 'Visible'}]}]}
    result = reconcile_metadata([payload])
    assert result['authors'] == ['Visible']
    assert {credit['name'] for credit in result['credits']} == {'Garbled', 'Visible'}
    assert result['metadata_conflicts'][0]['resolution'] == 'evidence_ranked'
    payload['credits'].pop()
    unresolved = reconcile_metadata([payload])
    assert unresolved['authors'] == []
    assert unresolved['metadata_conflicts'][0]['resolution'] == 'unresolved'


def test_isbn_checksum_and_edition_identifiers():
    assert valid_isbn('978-0-306-40615-7') == '9780306406157'
    assert valid_isbn('9780306406158') is None
    assert valid_isbn('0-306-40615-2') == '0306406152'


def test_fallback_selection_bounded_deterministic_no_reference_fields():
    pages = [{'page_number': number, 'text': text, 'reference_title': 'ignored'} for number, text in
             [(9, 'published edition'), (4, 'copyright published'), (2, 'Illustrated by Artist'), (1, 'story')]]
    assert [page['page_number'] for page in fallback_page_candidates(pages)] == [4, 9]
    assert [page['page_number'] for page in fallback_page_candidates(pages, exclude=[4])] == [9, 2]


def test_migration_additive_idempotent_preserves_manual_fields_and_legacy():
    old = {'title': 'Book', 'publisher': 'student', 'published': 1998, 'last_updated': 'original'}
    record = BookRecord('book', old)
    plans, flags = plan_migration([record])
    assert not flags and len(plans) == 1
    updates = plans[0].updates
    assert 'publisher' not in updates and 'published' not in updates
    assert updates['legacy_publication']['publisher'] == 'student'
    assert updates['photographed_edition']['imprint'] is None
    updated = {**old, **updates}
    assert plan_migration([BookRecord('book', updated)])[0] == []
    partial = {**old, 'photographed_edition': {'imprint': 'Manual correction'}}
    assert plan_migration([BookRecord('book', partial)])[0] == []


def test_migration_dry_run_budget_precedes_ledger(tmp_path):
    path = tmp_path / 'books.json'
    path.write_text(json.dumps({'books': {'book': {'title': 'Book', 'published': 1998}}}))
    calls = []
    result = run_migration([BookRecord('book', {'title': 'Book'})], log=calls.append)
    assert result['written'] == 0
    assert calls[0].startswith('Firestore budget')
    assert 'write cap 0' in calls[0]
    assert main(['--snapshot', str(path)]) == 0


def test_migration_conflicting_checkpoint_rejects_all_writes(tmp_path):
    path = tmp_path / 'checkpoint.json'
    path.write_text(json.dumps({'migration_version': 'publication-migration-v1', 'source': 'offline',
                               'completed': {'book': {'original_hash': 'stale'}}}))
    writes = []
    with pytest.raises(ValueError, match='Conflicting source'):
        run_migration([BookRecord('book', {'title': 'Book'}, 'version')], execute=True,
                      checkpoint=path, apply_update=writes.append, log=lambda _: None)
    assert not writes


def test_finalize_metadata_uses_ocr_without_extra_call(monkeypatch):
    import utilities
    initial = reconcile_metadata([{'title': {'value': 'Story', 'evidence': [{'page_number': 1, 'quote': 'Story'}]}}])
    initial['metadata_page_numbers'] = [1]
    monkeypatch.setattr(utilities, 'extract_joint_metadata', lambda *a, **k: pytest.fail('unnecessary vision'))
    result = utilities.finalize_photo_metadata(initial, [
        {'page_number': 2, 'text': 'Written by Writer\nIllustrated by Artist'}], object(),
        images_by_number={2: b'fake'})
    assert result['authors'] == ['Writer']
    assert result['illustrators'] == ['Artist']
    assert result['metadata_finalization']['additional_vision_calls'] == 0


def test_finalize_metadata_caps_fallback_and_is_idempotent(monkeypatch):
    import utilities
    initial = reconcile_metadata([])
    calls = []
    def fake_extract(pages, client, **kwargs):
        calls.append(pages)
        return reconcile_metadata([])
    monkeypatch.setattr(utilities, 'extract_joint_metadata', fake_extract)
    pages = [{'page_number': n, 'text': 'copyright'} for n in [4, 3, 2]]
    result = utilities.finalize_photo_metadata(initial, pages, object(), images_by_number={n: b'x' for n in [2, 3, 4]})
    assert [n for n, _ in calls[0]] == [2, 3]
    assert result['metadata_finalization']['additional_vision_calls'] == 1
    utilities.finalize_photo_metadata(result, pages, object(), images_by_number={2: b'x'})
    assert len(calls) == 1


def test_book_fields_roundtrip_new_targets_and_keep_legacy(monkeypatch):
    from data_structures.book import Book
    from data_structures.base_structure import Field
    book = Book(db_object={'title': 'Book', 'published': 1980, 'publisher': 'legacy-ref'})
    assert isinstance(Book.photographed_edition, Field)
    book.apply_publication_form({'photographed_edition': {'imprint': 'New', 'publishing_house': 'House', 'year': '2010'}})
    assert book.published == 1980 and book.publisher == 'legacy-ref'
    assert book.to_dict()['photographed_edition']['publishing_house'] == 'House'
    assert book.first_publication['year'] is None
    assert book.legacy_publication['published_year'] == 1980


def test_joint_same_page_roles_single_call_and_actual_page_labels(monkeypatch):
    import utilities
    calls = []
    payload = {'title': {'value': 'Story', 'evidence': [{'page_number': 7, 'quote': 'Story'}]},
               'credits': [], 'publisher_statements': [], 'isbns': [], 'conflicts': []}
    monkeypatch.setattr(utilities, 'locate_key_pages', lambda *args: {'title_page': 2, 'copyright_page': 2})
    monkeypatch.setattr(utilities, 'get_ai_settings', lambda: {'metadata_model': 'fixture', 'extraction_max_edge': 1568})
    def fake_vision(client, images, prompt, **kwargs):
        calls.append((images, prompt, kwargs))
        return deepcopy(payload), json.dumps(payload)
    monkeypatch.setattr(utilities, 'vision_json', fake_vision)
    result = utilities.extract_photo_first_metadata([(3, b'cover'), (7, b'title/copyright')], object(),
                                                    isbn_lookup=lambda _: pytest.fail('no ISBN'))
    assert len(calls) == 1
    assert calls[0][0] == [b'title/copyright', b'cover']
    assert '"page_number": 7' in calls[0][1]
    assert calls[0][2]['reject_partial'] is True
    assert result['title'] == 'Story'
    assert result['metadata_prompt_manifest']['rendered_prompt_utf8'] == calls[0][1]


@pytest.mark.parametrize('payload', [None, {}, {'title': None},
    {'title': {'value': None, 'evidence': []}, 'credits': 'broken',
     'publisher_statements': [], 'isbns': [], 'conflicts': []}])
def test_joint_invalid_response_not_completed_and_raw_survives(monkeypatch, payload):
    import utilities
    monkeypatch.setattr(utilities, 'get_ai_settings', lambda: {'metadata_model': 'fixture', 'extraction_max_edge': 1568})
    monkeypatch.setattr(utilities, 'vision_json', lambda *a, **k: (payload, 'invalid retained raw'))
    result = utilities.extract_joint_metadata([(7, b'photo')], object())
    assert result['metadata_extraction_status'] == 'invalid_response'
    assert result['raw'] == 'invalid retained raw'
    assert result['first_publication']['year'] is None


def test_joint_rejects_evidence_for_unsupplied_page(monkeypatch):
    import utilities
    payload = {'title': {'value': 'Story', 'evidence': [{'page_number': 99, 'quote': 'Story'}]},
               'credits': [], 'publisher_statements': [], 'isbns': [], 'conflicts': []}
    monkeypatch.setattr(utilities, 'get_ai_settings', lambda: {'metadata_model': 'fixture', 'extraction_max_edge': 1568})
    monkeypatch.setattr(utilities, 'vision_json', lambda *a, **k: (payload, 'retained'))
    result = utilities.extract_joint_metadata([(7, b'photo')], object())
    assert result['metadata_extraction_status'] == 'invalid_response'
    assert result['title'] is None


def test_finalize_invalid_fallback_is_visible_failure(monkeypatch):
    import utilities
    monkeypatch.setattr(utilities, 'extract_joint_metadata', lambda *a, **k:
                        {'metadata_extraction_status': 'invalid_response', 'raw': 'truncated'})
    result = utilities.finalize_photo_metadata(reconcile_metadata([]),
        [{'page_number': 2, 'text': 'copyright'}], object(), images_by_number={2: b'fake'})
    assert result['metadata_finalization']['status'] == 'failed'
    assert result['metadata_fallback_response']['raw'] == 'truncated'


def test_v2_invalid_isbn_never_calls_lookup_and_lookup_never_fills_imprint():
    import utilities
    metadata = publication_projection([])
    metadata['isbn'] = '9780306406158'
    utilities.apply_isbn_metadata(metadata, isbn_lookup=lambda _: pytest.fail('invalid ISBN call'))
    assert metadata['isbn_lookup']['status'] == 'invalid_isbn'
    metadata['isbn'] = '9780306406157'
    utilities.apply_isbn_metadata(metadata, isbn_lookup=lambda _: {'publisher': 'Wrong scope',
        'edition_identifiers': [{'type': 'ISBN_13', 'identifier': '9780306406157'}]})
    assert metadata['publisher'] is None
    assert metadata['isbn_enrichment_candidates']['edition_identifiers']
