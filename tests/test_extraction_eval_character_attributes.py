"""Source-coded attributes on the ratified character matching graph."""
import json
from collections import Counter

import pytest

from scripts.extraction_eval.scoring import score_character_attributes, score_character_recall


FIELDS = ('gender', 'human', 'protagonist')


def character(name='cat', **values):
    return {'name': name, 'gender': 'Female', 'human': False, 'protagonist': True, **values}


def score(recorded, predicted):
    return score_character_attributes(score_character_recall(recorded, predicted))


def assert_confusion_totals(attributes, matched_pairs):
    for result in attributes.values():
        agreement = result['matched_pair_agreement']
        assert sum(sum(row.values()) for row in agreement['confusion'].values()) == matched_pairs
        assert agreement['matched_pairs'] == matched_pairs
        assert agreement['denominator'] + agreement['excluded_reference'] == matched_pairs


@pytest.mark.parametrize('gender', ['Female', 'Male', 'Non-specific', 'Transgender'])
def test_identical_casts(gender):
    cast = [character(gender=gender), character('fox', human=True, protagonist=False)]
    results = score(cast, cast)
    assert set(results) == set(FIELDS)  # No imported plural/ethnicity/disability defaults.
    for result in results.values():
        assert result['recall'] == result['strict_recall'] == 1
        assert result['denominator'] == 2
        assert result['matched_pair_agreement']['agreement'] == 1
    assert_confusion_totals(results, 2)


@pytest.mark.parametrize('field,value', [('gender', 'Male'), ('human', True), ('protagonist', False)])
def test_wrong_code_lowers_only_its_field(field, value):
    results = score([character(), character('fox')], [character(**{field: value}), character('fox')])
    for name, result in results.items():
        assert result['recall'] == result['strict_recall'] == (0.5 if name == field else 1)
    assert_confusion_totals(results, 2)


def test_omission_lowers_every_headline_but_not_matched_pair_agreement():
    results = score([character(), character('fox')], [character(), character('rabbit')])
    for result in results.values():
        assert result['recall'] == result['strict_recall'] == 0.5
        assert result['matched_pair_agreement']['agreement'] == 1
    assert_confusion_totals(results, 1)


def test_fuller_name_compares_codes_in_headline_only():
    results = score([character()], [character('the old cat')])
    for result in results.values():
        assert result['recall'] == 1
        assert result['strict_recall'] == 0
        assert result['matched_pair_agreement']['agreement'] == 1
        assert result['strict_matched_pair_agreement']['agreement'] is None
    assert score([character()], [character('old cat', human=True)])['human']['recall'] == 0


@pytest.mark.parametrize('field', FIELDS)
@pytest.mark.parametrize('value,reason', [(None, 'absent'), ('', 'absent'), ('unexpected', 'unmapped')])
def test_uncoded_recorded_character_leaves_only_that_denominator(field, value, reason):
    results = score([character(**{field: value}), character('fox')], [character(), character('fox')])
    result = results[field]
    assert result['denominator'] == result['numerator'] == 1
    assert result['excluded_reference'] == [{'reference': 0, 'reason': reason}]
    assert result['excluded_reference_counts'] == {reason: 1}
    assert result['matched_pair_agreement']['excluded_reference'] == 1
    assert sum(result['matched_pair_agreement']['confusion'][reason].values()) == 1
    assert all(results[other]['denominator'] == 2 for other in FIELDS if other != field)
    assert_confusion_totals(results, 2)


@pytest.mark.parametrize('field', FIELDS)
@pytest.mark.parametrize('value,label', [(None, 'absent'), ('', 'absent'), ('unexpected', 'invalid'),
                                      ([], 'invalid'), ({}, 'invalid')])
def test_absent_or_invalid_prediction_scores_zero(field, value, label):
    result = score([character()], [character(**{field: value})])[field]
    assert result['recall'] == result['strict_recall'] == 0
    assert result['denominator'] == 1
    assert result['matched_pair_agreement']['agreement'] == 0
    assert sum(row[label] for row in result['matched_pair_agreement']['confusion'].values()) == 1


def test_absent_on_both_sides_is_not_an_agreement():
    for result in score([{'name': 'cat'}], [{'name': 'cat'}]).values():
        assert result['denominator'] == result['numerator'] == 0
        assert result['recall'] is result['strict_recall'] is None
        assert result['matched_pair_agreement']['agreement'] is None
        assert result['matched_pair_agreement']['confusion']['absent']['absent'] == 1


@pytest.mark.parametrize('source,canonical', [
    ({'gender': 'F', 'human': ' H ', 'is_protagonist': 1}, character(human=True)),
    ({'gender': 'M', 'human': 'NH', 'is_protagonist': 0}, character(gender='Male', protagonist=False)),
    ({'gender': 'NGS', 'human': False, 'protagonist': True}, character(gender='Non-specific')),
])
@pytest.mark.parametrize('nested', [False, True])
def test_source_mapping_and_snapshot_raw_records(source, canonical, nested):
    recorded = {'name': 'cat', **({'raw': source} if nested else source)}
    for result in score([recorded], [canonical]).values():
        assert result['recall'] == result['strict_recall'] == 1


@pytest.mark.parametrize('field,value', [
    ('gender', 'NBT'), ('gender', 'Non-binary'), ('gender', 'female'),
    ('human', 'NO'), ('human', 'NGS'), ('human', 1), ('human', 'False'),
    ('protagonist', '0'), ('protagonist', 1.0),
])
def test_unmapped_recorded_codes_do_not_receive_import_defaults(field, value):
    result = score([character(**{field: value})], [character()])[field]
    assert result['denominator'] == 0
    assert result['excluded_reference_counts'] == {'unmapped': 1}


@pytest.mark.parametrize('field,value', [('gender', 'F'), ('human', 'NH'), ('human', 0), ('protagonist', 1)])
def test_pipeline_requires_canonical_codes(field, value):
    assert score([character()], [character(**{field: value})])[field]['recall'] == 0


def test_each_field_can_choose_a_different_maximum_pairing():
    recorded = [character(gender='Male', human=True), character(gender='Female', human=False)]
    predicted = [character(gender='Male', human=False), character(gender='Female', human=True)]
    characters = score_character_recall(recorded, predicted)
    results = score_character_attributes(characters)
    assert len(characters['ambiguous_matches']) == 2
    assert results['gender']['recall'] == results['human']['recall'] == 1
    assert results['gender']['matches'] != results['human']['matches']
    assert len(results['gender']['pairing_changes']) == 2
    assert results['human']['pairing_changes'] == []
    assert results['gender']['matched_pair_agreement']['agreement'] == 0
    assert results['human']['matched_pair_agreement']['agreement'] == 1
    assert_confusion_totals(results, 2)


def test_empty_cast_and_additions_have_undefined_attribute_recall():
    characters = score_character_recall([], [character()])
    assert characters['unmatched_prediction'] == [0]
    results = score_character_attributes(characters)
    for result in results.values():
        assert result['denominator'] == 0
        assert result['recall'] is result['strict_recall'] is None
    assert_confusion_totals(results, 0)


def test_exposed_dev20_attribute_regression():
    from conftest import load_dev20_predictions
    from scripts.extraction_eval.evidence import evidence_dir

    path = evidence_dir() / 'dev20-snap' / 'manifest.json'
    if not path.is_file():
        pytest.skip(f'Dev20 evidence file is absent: {path}')
    predictions = load_dev20_predictions()['books']
    books = json.loads(path.read_text())['books']
    totals = {field: Counter() for field in FIELDS}
    disagreements = {field: [] for field in FIELDS}
    character_totals = Counter()
    changed = set()
    for book in books:
        book_id = book['id']
        if book_id not in predictions:
            continue
        recorded = book['reference']['characters']['value']
        predicted = predictions[book_id]['characters']['value']
        characters = score_character_recall(recorded, predicted)
        character_totals.update({key: characters[key] for key in (
            'denominator', 'strict_numerator', 'tolerant_numerator')})
        results = score_character_attributes(characters)
        assert_confusion_totals(results, characters['tolerant_numerator'])
        for field, result in results.items():
            strict = result['strict_matched_pair_agreement']
            totals[field].update({key: result[key] for key in (
                'numerator', 'strict_numerator', 'denominator')})
            totals[field].update(strict_agreement=strict['numerator'], strict_pairs=strict['denominator'],
                                 agreement=result['matched_pair_agreement']['numerator'],
                                 excluded=len(result['excluded_reference']))
            for pair in strict['disagreements']:
                r, p = pair['reference'], pair['prediction']
                disagreements[field].append((book_id, recorded[r]['name'], predicted[p]['name'],
                                             recorded[r]['raw'].get(field), predicted[p].get(field)))
            changed.update((book_id, pair['reference']) for pair in result['pairing_changes'])
    assert character_totals == {'denominator': 96, 'strict_numerator': 82, 'tolerant_numerator': 90}
    for field, headline, strict_expected in [('gender', 81, 73), ('human', 70, 63), ('protagonist', 83, 77)]:
        assert totals[field] == {'denominator': 96, 'numerator': headline, 'strict_numerator': strict_expected,
                                 'agreement': headline, 'strict_agreement': strict_expected,
                                 'strict_pairs': 82, 'excluded': 0}, disagreements[field]
    assert changed == set()


def test_found_agreement_and_extra_characters_reach_numeric_reports():
    from scripts.extraction_eval.reporting import scan_metric, summarize_scan_scores, scan_summary_markdown
    refs = [character('cat', human=True), character('fox'), character('owl')]
    preds = [character('cat', gender='Male', human=True), character('fox', human=None), character('rabbit')]
    chars = score_character_recall(refs, preds)
    row = {'id': 'fixture', 'characters': chars, 'attributes': score_character_attributes(chars)}
    assert chars['extra_characters'] == 1
    for field in ('gender', 'human'):
        assert scan_metric(row, field + '_found_agreement') == {'value': .5, 'numerator': 1, 'denominator': 2}
        assert scan_metric(row, field)['value'] == 1 / 3
    assert scan_metric(row, 'protagonist_found_agreement')['value'] == 1
    summary = summarize_scan_scores([row], {'book_strata': {'fixture': 'alias_zero'}}, cohort='tune')
    rendered = scan_summary_markdown(summary)
    assert 'Gender agreement among found characters: mean 50.00%; pooled 1/2' in rendered
    assert 'Extra characters (unmatched predictions): mean 1.0000; total 1;' in rendered
    chars = score_character_recall(refs, [character('rabbit')])
    row.update(characters=chars, attributes=score_character_attributes(chars))
    assert chars['extra_characters'] == 1
    assert scan_metric(row, 'gender_found_agreement')['value'] is None
