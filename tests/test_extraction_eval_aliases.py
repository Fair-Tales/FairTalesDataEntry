"""Per-book alias figures use the ratified cast matching and existing M1 checks."""
import json
from collections import Counter

import pytest

from scripts.extraction_eval.alias_metrics import score_alias_foundation, score_aliases
from scripts.extraction_eval.scoring import score_character_recall


def score(recorded, predicted, text=''):
    return score_aliases(score_character_recall(recorded, predicted), [('p1', text)])


def test_coverage_requires_explicit_short_form_and_deduplicates_within_owner():
    recorded = [{'name': 'cat', 'aliases': ['CAT', 'the cat']}]
    missing = score(recorded, ['The Old Cat'])['explicit_alias_coverage']
    recovered = score(recorded, [{'name': 'The Old Cat', 'aliases': ['cat']}])['explicit_alias_coverage']
    assert (missing['numerator'], missing['denominator'], missing['coverage']) == (0, 1, 0)
    assert (recovered['numerator'], recovered['denominator'], recovered['coverage']) == (1, 1, 1)
    assert missing['unresolved_owner_count'] == 0


def test_primary_name_is_not_in_coverage_denominator_but_recovers_recorded_alias():
    result = score([{'name': 'cat', 'aliases': ['old cat']}], ['old cat'])
    assert result['explicit_alias_coverage']['denominator'] == 1
    assert result['explicit_alias_coverage']['entries'][0]['surface'] == 'cat'
    assert result['alias_recall']['numerator'] == result['alias_recall']['denominator'] == 1


def test_duplicates_are_not_additional_forms_and_primary_name_is_a_form():
    result = score(['cat'], [{'name': 'cat', 'aliases': ['CAT', 'the cat', 'kitty', 'a KITTY']}])
    extra = result['additional_aliases']
    assert (extra['numerator'], extra['denominator']) == (1, 2)
    assert (extra['duplicates']['numerator'], extra['duplicates']['denominator']) == (3, 5)
    assert [row['surface'] for row in extra['entries']] == ['kitty']
    assert extra['per_character'] == [{'prediction': 0, 'reference': 0, 'distinct_forms': 2,
                                      'additional_forms': 1, 'emitted_forms': 5, 'duplicates': 3}]
    assert score(['cat'], ['old cat'])['additional_aliases']['entries'][0]['field'] == 'name'


def test_additional_forms_grounding_and_shared_form_ambiguity():
    result = score(['cat', 'dog'], [{'name': 'cat', 'aliases': ['friend', 'ghost']},
                                  {'name': 'dog', 'aliases': ['the friend']}], 'Cat and dog greet a friend.')
    extra = result['additional_aliases']
    assert extra['grounding'] == {'numerator': 2, 'denominator': 3, 'share': 2 / 3}
    assert [(row['surface'], row['tier']) for row in extra['entries']] == [
        ('friend', 'exact'), ('ghost', 'ungrounded'), ('the friend', 'determiner_stripped')]
    assert len(extra['ambiguous_forms']) == 1
    assert extra['ambiguous_forms'][0]['tokens'] == ['friend']
    assert [row['prediction'] for row in extra['ambiguous_forms'][0]['owners']] == [0, 1]
    assert result['groundedness']['numerator'] == 4
    assert result['groundedness']['denominator'] == 5


def test_no_recorded_alias_has_undefined_recall_and_empty_output_is_zero():
    result = score(['cat'], ['cat'])['alias_recall']
    assert result['denominator'] == 0
    assert result['recall'] is None
    result = score([{'name': 'cat', 'aliases': ['kitty']}], [])
    assert result['alias_recall']['denominator'] == 1
    assert result['alias_recall']['recall'] == 0
    assert result['groundedness']['denominator'] == 0
    assert result['groundedness']['share'] is None


@pytest.mark.parametrize('predicted', [
    [{'name': 'cat', 'aliases': ['kitty']}],
    [{'name': 'cat', 'aliases': ['kitty']}, {'name': 'cat', 'aliases': ['kitty']}],
])
def test_variable_owners_are_excluded_even_when_unmatched_in_representative(predicted):
    recorded = [{'name': 'cat', 'aliases': ['kitty']}, {'name': 'cat', 'aliases': ['kitty']}]
    result = score(recorded, predicted)
    coverage = result['explicit_alias_coverage']
    assert coverage['unresolved_owners'] == [0, 1]
    assert coverage['unresolved_owner_count'] == 2
    assert coverage['numerator'] == coverage['denominator'] == 0
    assert coverage['coverage'] is None
    # One predicted surface-owner pair cannot recover both recorded aliases.
    assert result['alias_recall']['numerator'] == len(predicted)
    assert result['alias_recall']['denominator'] == 2


def test_multiple_candidate_edges_can_still_have_invariant_owners():
    recorded = [{'name': 'Rabbit', 'aliases': ['Bunny']}, {'name': 'Rabbit'}]
    result = score(recorded, ['Rabbit', 'Bunny'])['explicit_alias_coverage']
    assert result['unresolved_owner_count'] == 0
    assert result['denominator'] == 1
    assert result['numerator'] == 0


def test_recall_requires_correct_owner_and_preserves_possessors():
    recorded = [{'name': 'cat', 'aliases': ['kitty']}, {'name': 'dog'}]
    result = score(recorded, [{'name': 'cat'}, {'name': 'dog', 'aliases': ['kitty']}])
    assert result['alias_recall']['numerator'] == 0
    assert result['alias_recall']['denominator'] == 1
    assert result['owner_matching'] == 'character_recall_representative_maximum'
    result = score([{'name': 'Mother Duck', 'aliases': ['mother']}],
                   [{'name': 'Mother Duck', 'aliases': ['his mother']}])
    assert result['alias_recall']['recall'] == 0
    assert result['additional_aliases']['numerator'] == 1


def test_one_recorded_owner_can_move_between_two_predictions():
    result = score([{'name': 'cat', 'aliases': ['kitty']}], ['cat', 'cat'])
    assert result['explicit_alias_coverage']['unresolved_owners'] == [0]
    assert result['explicit_alias_coverage']['denominator'] == 0


def test_grounding_reuses_m1_without_cross_page_or_substring_hits():
    predicted = [{'name': 'cat', 'aliases': ['cat', 'CAT', 'his cat', 'old cat']}]
    pages = [('p1', 'A cat and an old'), ('p2', 'catapult')]
    result = score_aliases(score_character_recall([], predicted), pages)['groundedness']
    foundation = score_alias_foundation({}, {'characters': {'value': predicted}, 'pages': {
        page_id: {'status': 'completed', 'text': text} for page_id, text in pages}})['M1']
    for output, legacy in ((result['aliases'], foundation), (result['names'], foundation['character_names'])):
        assert output['numerator'] == legacy['grounded']
        assert output['denominator'] == legacy['aliases']
        assert output['share'] == legacy['lexical_grounding']
    assert result['aliases']['denominator'] == 3  # M1 deduplicates case within aliases.
    assert result['aliases']['ungrounded'] == 1
    assert result['denominator'] == 4  # Name and alias fields stay separate, as in M1.


def test_exposed_dev20_alias_regression():
    from conftest import load_dev20_predictions
    from scripts.extraction_eval.evidence import evidence_dir

    path = evidence_dir() / 'dev20-snap' / 'manifest.json'
    if not path.is_file():
        pytest.skip(f'Dev20 evidence file is absent: {path}')
    predictions = load_dev20_predictions()['books']
    books = json.loads(path.read_text())['books']
    # Coverage n/d, unresolved owners, recall n/d, extra n/d, duplicates,
    # grounded extras, combined grounding n/d. Pooled counts, not book means.
    expected = {
        'a_squash_and_a_squeeze': (0, 0, 0, 0, 0, 0, 6, 0, 0, 6, 6),
        'all_for_one': (2, 2, 0, 0, 0, 4, 10, 0, 4, 10, 10),
        'all_year_round': (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        'cave_baby': (2, 3, 0, 1, 1, 14, 22, 0, 13, 21, 22),
        'how_the_grinch_stole_christmas': (0, 1, 0, 0, 0, 15, 18, 0, 15, 18, 18),
        "it's_christmas": (0, 0, 0, 0, 0, 0, 6, 0, 0, 6, 6),
        'norman_the_slug_with_the_silly_shell': (0, 0, 0, 0, 0, 1, 4, 0, 1, 4, 4),
        'room_on_the_broom': (1, 1, 0, 0, 0, 2, 9, 0, 2, 9, 9),
        'santa_is_coming_to_devon': (1, 1, 0, 0, 0, 12, 16, 1, 9, 14, 17),
        'sharing_a_shell': (2, 2, 0, 2, 2, 0, 7, 0, 0, 7, 7),
        'the_book_with_no_pictures': (0, 0, 0, 0, 0, 11, 11, 0, 11, 11, 11),
        'the_enormous_crocodile': (1, 1, 0, 0, 0, 19, 29, 0, 14, 24, 29),
        'the_little_bully': (0, 0, 0, 0, 0, 4, 9, 0, 1, 6, 9),
        'the_runaway_pea': (2, 4, 0, 0, 2, 8, 13, 0, 7, 11, 13),
        'the_same_but_different_too': (0, 0, 0, 0, 0, 2, 3, 0, 2, 3, 3),
        'the_storm_whale': (0, 1, 0, 0, 0, 7, 9, 0, 7, 9, 9),
        'the_three_little_pigs': (1, 4, 0, 0, 1, 10, 16, 4, 7, 17, 20),
        'the_ugly_duckling': (2, 7, 0, 2, 4, 10, 15, 0, 9, 14, 15),
        "we're_going_on_a_bear_hunt": (0, 0, 0, 0, 0, 2, 4, 0, 0, 2, 4),
        'wiggling_worms_at_work': (0, 0, 2, 0, 0, 4, 5, 0, 4, 5, 5),
    }
    measured, totals = {}, Counter()
    for book in books:
        if book['id'] not in predictions:
            continue
        prediction = predictions[book['id']]
        characters = score_character_recall(book['reference']['characters']['value'],
                                            prediction['characters']['value'])
        pages = [(key, page['text']) for key, page in prediction['pages'].items()
                 if page['status'] == 'completed']
        result = score_aliases(characters, pages)
        cov, recall = result['explicit_alias_coverage'], result['alias_recall']
        extras, grounding = result['additional_aliases'], result['groundedness']
        measured[book['id']] = (cov['numerator'], cov['denominator'], cov['unresolved_owner_count'],
                                recall['numerator'], recall['denominator'], extras['numerator'],
                                extras['denominator'], extras['duplicates']['numerator'],
                                extras['grounding']['numerator'], grounding['numerator'], grounding['denominator'])
        totals.update(alias_books=bool(recall['denominator']), ambiguous=len(extras['ambiguous_forms']),
                      exact=grounding['exact'], stripped=grounding['determiner_stripped'],
                      ungrounded=grounding['ungrounded'])
        totals.update({f'alias_{key}': grounding['aliases'][key] for key in (
            'numerator', 'denominator', 'exact', 'determiner_stripped', 'ungrounded')})
    assert measured == expected
    assert tuple(map(sum, zip(*measured.values()))) == (14, 27, 2, 5, 10, 125, 212, 5, 106, 197, 217)
    assert totals == dict(alias_books=5, ambiguous=0, exact=186, stripped=11, ungrounded=20,
                          alias_numerator=104, alias_denominator=112, alias_exact=95,
                          alias_determiner_stripped=9, alias_ungrounded=8)
