"""Frozen content-exposure ledger for development/holdout separation."""

EXPOSURE_VERSION = "pipeline-exposure-2026-11-05-v1"
DEV20 = frozenset(
    {
        "a_squash_and_a_squeeze",
        "all_for_one",
        "all_year_round",
        "cave_baby",
        "how_the_grinch_stole_christmas",
        "it's_christmas",
        "norman_the_slug_with_the_silly_shell",
        "room_on_the_broom",
        "santa_is_coming_to_devon",
        "sharing_a_shell",
        "the_book_with_no_pictures",
        "the_enormous_crocodile",
        "the_little_bully",
        "the_runaway_pea",
        "the_same_but_different_too",
        "the_storm_whale",
        "the_three_little_pigs",
        "the_ugly_duckling",
        "we're_going_on_a_bear_hunt",
        "wiggling_worms_at_work",
    }
)
ROTATION_EXPOSED = frozenset(
    {
        "born_to_be_a_butterfly",
        "bottoms_up!",
        "elmer_on_stilts",
        "kipper's_a_to_z",
        "knock_knock_dinosaur",
        "lenny_makes_a_wish",
        "lost_in_snow",
        "the_bad-tempered_ladybird",
        "the_cave",
        "the_smartest_giant_in_town",
        "who_will_save_us",
    }
)
EXPOSURE_RECORDS = (
    [
        {"book_id": book, "reason": "dev20_baseline_and_prompt_development"}
        for book in sorted(DEV20)
    ]
    + [
        {"book_id": book, "reason": "historical_rotation_fixture"}
        for book in sorted(ROTATION_EXPOSED)
    ]
    + [
        {"book_id": "the_gruffalo", "reason": "active_metadata_example"},
        {
            "book_id": "the_gruffalo's_child",
            "reason": "known_example_linkage_to_the_gruffalo",
        },
        {
            "book_id": "a_letter_to_santa",
            "reason": "incidental_nested_page_text_exposure_during_design",
        },
    ]
)
EXPOSED_IDS = frozenset(record["book_id"] for record in EXPOSURE_RECORDS)


def exposure_ledger(additional=None):
    records = [dict(record) for record in EXPOSURE_RECORDS]
    for book in sorted(set(additional or []) - EXPOSED_IDS):
        records.append({"book_id": book, "reason": "current_run_selection"})
    return {
        "version": EXPOSURE_VERSION,
        "source": "planning/pipeline_improvement_design_2026-11-05.md#8",
        "records": records,
    }
