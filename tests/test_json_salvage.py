"""Tests for ``utilities.salvage_json`` (#183).

The pilot hit a real character-detection failure — ``Expecting ',' delimiter:
line 24 column 116 (char 2700)`` — where one malformed/truncated model reply
threw the whole run away. ``salvage_json`` recovers the usable prefix of such
replies; these tests lock in the recovery behaviours and the give-up cases.
Pure function — no Streamlit runtime, no network.
"""

from utilities import salvage_json


def test_clean_json_object_parses():
    assert salvage_json('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}


def test_fenced_json_parses():
    raw = '```json\n{"characters": [{"name": "Tom"}]}\n```'
    assert salvage_json(raw) == {"characters": [{"name": "Tom"}]}


def test_leading_prose_before_object_is_ignored():
    raw = 'Here is the JSON you asked for: {"a": 1}'
    assert salvage_json(raw) == {"a": 1}


def test_trailing_prose_after_object_is_ignored():
    raw = '{"a": 1} I hope that helps!'
    assert salvage_json(raw) == {"a": 1}


def test_truncated_reply_recovers_complete_entries():
    # Cut off mid-way through the second character (max_tokens truncation).
    raw = (
        '{"characters": ['
        '{"name": "Tom", "aliases": ["the boy"], "gender": "Male"}, '
        '{"name": "Gran", "alia'
    )
    assert salvage_json(raw) == {
        "characters": [
            {"name": "Tom", "aliases": ["the boy"], "gender": "Male"},
        ]
    }


def test_missing_comma_recovers_entries_before_the_error():
    # The pilot's failure shape: a stray syntax error between two elements.
    raw = (
        '{"characters": ['
        '{"name": "A"}, {"name": "B"} {"name": "C"}]}'
    )
    assert salvage_json(raw) == {"characters": [{"name": "A"}, {"name": "B"}]}


def test_brace_inside_string_does_not_fool_the_repair():
    raw = '{"characters": [{"name": "smi}ley"}, {"name": "Gr'
    assert salvage_json(raw) == {"characters": [{"name": "smi}ley"}]}


def test_unrecoverable_input_returns_none():
    assert salvage_json("no json here at all") is None
    assert salvage_json("") is None
    assert salvage_json(None) is None
    # An object that never closes anything parseable.
    assert salvage_json('{"a": ') is None


def test_mismatched_brackets_are_not_wrongly_repaired():
    # ']' closing a '{' cannot be fixed by appending — must give up cleanly.
    assert salvage_json('{"a": [1, 2}') is None
