"""Ratified character recall, independent of the historical forced-edge scorer."""
import json

import pytest

from scripts.extraction_eval.normalization import normalize_character_name
from scripts.extraction_eval.scoring import score_character_recall


@pytest.mark.parametrize('cast', [
    ['Rabbit', 'Fox'],
    ['A', 'An', 'The'],
    [{'name': 'Rabbit', 'aliases': ['friend']}, {'name': 'Fox', 'aliases': ['friend']}],
    [{'name': 'Rabbit'}, {'name': 'Rabbit'}],
])
def test_self_cast_has_full_recall_even_with_shared_names_or_aliases(cast):
    score = score_character_recall(cast, cast)
    assert score['strict_recall'] == score['tolerant_recall'] == 1
    assert score['strict_numerator'] == score['tolerant_numerator'] == len(cast)
    assert score['unmatched_reference'] == []
    assert all(pair['kind'] == 'strict' for pair in score['matches'])


@pytest.mark.parametrize('recorded,predicted', [
    ('cat', 'The Old Cat'), ('The Old Cat', 'cat'),
    ('big bear', 'the big brown bear'), ('big brown bear', 'big bear'),
    ("Noi's dad", "Noi’s old dad"), ('my cat', 'my old cat'),
    ("dogs' toy", "dogs’ old toy"),
])
def test_unique_fuller_or_shorter_name(recorded, predicted):
    score = score_character_recall([recorded], [predicted])
    assert score['strict_numerator'] == 0
    assert score['tolerant_numerator'] == score['denominator'] == 1
    assert score['strict_recall'] == 0
    assert score['tolerant_recall'] == 1
    assert score['matches'] == [{'reference': 0, 'prediction': 0, 'kind': 'tolerant'}]


@pytest.mark.parametrize('recorded,predicted', [
    (['cat'], ['old cat', 'young cat']),
    (['old cat', 'young cat'], ['cat']),
])
def test_two_possible_entities_are_ambiguous_on_either_side(recorded, predicted):
    score = score_character_recall(recorded, predicted)
    assert score['tolerant_numerator'] == 0
    assert score['unmatched_reference'] == list(range(len(recorded)))
    assert len(score['ambiguous_candidates']) == 2
    assert all(pair['reason'] == 'multiple_possible_opposite_entities'
               for pair in score['ambiguous_candidates'])


def test_repeated_surfaces_on_one_entity_count_once():
    score = score_character_recall(
        [{'name': 'cat', 'aliases': ['cat', {'name': 'the CAT'}]}],
        [{'name': 'old cat', 'aliases': ['the old cat', 'Old Cat']}])
    assert score['tolerant_recall'] == 1
    assert score['ambiguous_candidates'] == []


@pytest.mark.parametrize('recorded,predicted', [
    ('Dad', "Noi's dad"), ('mum', 'my mum'), ('cat', 'your cat'),
    ('cat', 'his cat'), ('cat', 'her cat'), ('cat', 'its cat'),
    ('cat', 'our cat'), ('cat', 'their cat'), ('cat', 'whose cat'),
    ("dogs' toy", 'dogs toy'), ("Noi's dad", "Tom's dad"),
    ("Noi's dad's cat", "dad's Noi's cat"),
    ('cat', 'cats'), ('cat', 'bobcat'), ('cat', 'cot'),
    ('brown big bear', 'big brown bear'), ('cat cat', 'old cat'),
    ('the', 'the cat'), ('', 'cat'),
])
def test_no_spelling_stemming_reordering_or_possessor_changes(recorded, predicted):
    score = score_character_recall([recorded], [predicted])
    assert score['tolerant_numerator'] == score['strict_numerator'] == 0
    assert score['unmatched_reference'] == [0]
    assert score['ambiguous_candidates'] == []


@pytest.mark.parametrize('recorded,predicted', [
    (['Dad'], [{'name': "Noi's dad", 'aliases': ['Dad']}]),
    ([{'name': "Noi's dad", 'aliases': [{'name': 'Dad'}]}], ['Dad']),
    ([{'name': 'Dad', 'aliases': ["Noi's dad"]}], ["Noi's dad"]),
])
def test_explicit_shared_surface_allows_possessive_and_plain_names(recorded, predicted):
    score = score_character_recall(recorded, predicted)
    assert score['strict_recall'] == score['tolerant_recall'] == 1


@pytest.mark.parametrize('article', ['A', 'An', 'The'])
@pytest.mark.parametrize('alias_on_reference', [False, True])
def test_article_only_alias_matches_strictly(article, alias_on_reference):
    named = [article]
    aliased = [{'name': 'Letter', 'aliases': [article]}]
    recorded, predicted = (aliased, named) if alias_on_reference else (named, aliased)
    score = score_character_recall(recorded, predicted)
    assert score['strict_recall'] == score['tolerant_recall'] == 1


@pytest.mark.parametrize('recorded,predicted', [
    ('cat', 'the cat'), ('A Cat', 'an CAT'), ('the cat', 'Cat'),
    ('CAFÉ’s １２ cats', "the café's 12 cats"),
])
def test_article_only_differences_and_frozen_unicode_normalization_are_strict(recorded, predicted):
    score = score_character_recall([recorded], [predicted])
    assert score['strict_recall'] == score['tolerant_recall'] == 1
    assert score['matches'][0]['kind'] == 'strict'


def test_possessor_signature_precedes_punctuation_tokenization():
    assert normalize_character_name("The dogs’ toy") == (('dogs', 'toy'), ("dogs'",))
    assert normalize_character_name("my Noi’s dogs’ toy") == (
        ('my', "noi's", 'dogs', 'toy'), ('my', "noi's", "dogs'"))


@pytest.mark.parametrize('recorded,predicted', [
    (['cat', 'old cat'], ['old cat']),
    (['old cat'], ['cat', 'old cat']),
    (['cat', 'old cat'], ['cat', 'very old cat']),
])
def test_tolerant_candidates_use_full_casts_and_never_displace_strict(recorded, predicted):
    score = score_character_recall(recorded, predicted)
    assert score['strict_numerator'] == score['tolerant_numerator'] == 1
    assert score['matches'][0]['kind'] == 'strict'
    assert score['ambiguous_candidates']


def test_maximum_matching_reassigns_an_earlier_pair_that_greedy_would_keep():
    # A greedy first-edge matcher gives r0->p0 and strands r1; optimum has two.
    score = score_character_recall(
        [{'name': 'Rabbit', 'aliases': ['Bunny']}, {'name': 'Rabbit'}],
        [{'name': 'Rabbit'}, {'name': 'Bunny'}])
    assert score['strict_recall'] == score['tolerant_recall'] == 1
    assert score['matches'] == [
        {'reference': 0, 'prediction': 1, 'kind': 'strict'},
        {'reference': 1, 'prediction': 0, 'kind': 'strict'}]


def test_empty_output_is_zero_and_empty_reference_is_undefined():
    score = score_character_recall(['cat'], [])
    assert score['strict_recall'] == score['tolerant_recall'] == 0
    assert score['unmatched_reference'] == [0]
    score = score_character_recall([], ['cat'])
    assert score['strict_recall'] is score['tolerant_recall'] is None
    assert score['denominator'] == 0


def test_exposed_dev20_character_recall_regression():
    from conftest import load_dev20_predictions
    from scripts.extraction_eval.evidence import evidence_dir

    path = evidence_dir() / 'dev20-snap' / 'manifest.json'
    if not path.is_file():
        pytest.skip(f'Dev20 evidence file is absent: {path}')
    predictions = load_dev20_predictions()['books']
    books = json.loads(path.read_text())['books']
    # Per-book counts make any future discrepancy identifiable, not just a total.
    expected = {
        'a_squash_and_a_squeeze': (6, 6, 6), 'all_for_one': (6, 6, 6),
        'all_year_round': (0, 0, 0), 'cave_baby': (8, 7, 8),
        'how_the_grinch_stole_christmas': (4, 3, 4), "it's_christmas": (6, 6, 6),
        'norman_the_slug_with_the_silly_shell': (3, 3, 3), 'room_on_the_broom': (7, 7, 7),
        'santa_is_coming_to_devon': (6, 4, 4), 'sharing_a_shell': (5, 5, 5),
        'the_book_with_no_pictures': (0, 0, 0), 'the_enormous_crocodile': (11, 10, 10),
        'the_little_bully': (5, 5, 5), 'the_runaway_pea': (5, 5, 5),
        'the_same_but_different_too': (1, 1, 1), 'the_storm_whale': (4, 2, 3),
        'the_three_little_pigs': (8, 6, 8), 'the_ugly_duckling': (7, 3, 6),
        "we're_going_on_a_bear_hunt": (2, 2, 2), 'wiggling_worms_at_work': (2, 1, 1),
    }
    measured, unmatched = {}, {}
    for book in books:
        book_id = book['id']
        if book_id not in predictions:
            continue
        recorded = book['reference']['characters']['value']
        predicted = predictions[book_id]['characters']['value']
        score = score_character_recall(recorded, predicted)
        measured[book_id] = tuple(score[key] for key in (
            'denominator', 'strict_numerator', 'tolerant_numerator'))
        unmatched[book_id] = [recorded[r]['name'] for r in score['unmatched_reference']]
    assert measured == expected, unmatched
    assert tuple(map(sum, zip(*measured.values()))) == (96, 82, 90)
    assert unmatched['the_storm_whale'] == ['Dad']
    assert unmatched['santa_is_coming_to_devon'] == ['elves', 'old reindeer']
