"""Fixed lexical/canonical outputs from before the harness dependency was removed."""
from collections import Counter
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import text_normalization as app
from alias_schema import CANONICAL_RULE_VERSION, canonical_bundle, derive_canonical
from data_structures.alias import Alias
from scripts import backfill_alias_canonical as backfill
from scripts.extraction_eval import normalization as harness


@pytest.mark.parametrize("raw,normalized,tokens,without_article,canonical", [
    (None, "", [], "", ""),
    ("", "", [], "", ""),
    (" \t\n", "", [], "", " \t\n"),
    ("???", "???", [], "", "???"),
    ("  THE Brown—Bear!\n", "the brown—bear!", ["the", "brown", "bear"],
     "brown bear", "the brown bear"),
    ("ＦＯＸ １２", "fox 12", ["fox", "12"], "fox 12", "fox 12"),
    ("Straße ﬁsh", "strasse fish", ["strasse", "fish"], "strasse fish", "strasse fish"),
    ("Cafe\u0301 ÉLAN", "café élan", ["café", "élan"], "café élan", "café élan"),
    ("猫\u3000狗", "猫 狗", ["猫", "狗"], "猫 狗", "猫 狗"),
    ("O’NEIL O‘Neil OʼNeil O＇Neil", "o'neil o'neil o'neil o'neil",
     ["o'neil"] * 4, "o'neil o'neil o'neil o'neil", "o'neil o'neil o'neil o'neil"),
    ("The fox’s cub", "the fox's cub", ["the", "fox's", "cub"],
     "fox's cub", "the fox's cub"),
    ("THE foxes’ cubs", "the foxes' cubs", ["the", "foxes", "cubs"],
     "foxes cubs", "the foxes cubs"),
    ("Her daughter", "her daughter", ["her", "daughter"], "daughter", "her daughter"),
    ("my your his her its our their whose", "my your his her its our their whose",
     ["my", "your", "his", "her", "its", "our", "their", "whose"],
     "your his her its our their whose", "my your his her its our their whose"),
    ("a the Fox", "a the fox", ["a", "the", "fox"], "the fox", "a the fox"),
    ("The", "the", ["the"], "the", "the"),
    ("mother-in-law rock‑and–roll", "mother-in-law rock‐and–roll",
     ["mother", "in", "law", "rock", "and", "roll"],
     "mother in law rock and roll", "mother in law rock and roll"),
    ("[ ILLEGIBLE ] [??] [Unclear] [unreadable]", "[?] [?] [?] [?]",
     ["[?]"] * 4, "[?] [?] [?] [?]", "[?] [?] [?] [?]"),
    ("'Fox' 🦊_bear", "'fox' 🦊_bear", ["fox", "bear"], "fox bear", "fox bear"),
])
def test_frozen_lexical_and_stored_canonical_outputs(
        raw, normalized, tokens, without_article, canonical):
    for module in (app, harness):
        assert module.normalize_text(raw).encode("utf-8") == normalized.encode("utf-8")
        assert [word.encode("utf-8") for word in module.tokenize(raw)] == [
            word.encode("utf-8") for word in tokens]
        assert module.normalize_name(raw) == " ".join(tokens)
        assert module.normalize_name(raw, strip_article=True) == without_article
    assert harness.token_counts(raw) == Counter(tokens)
    assert derive_canonical(raw).encode("utf-8") == canonical.encode("utf-8")
    assert canonical_bundle(raw)["canonical_form"] == canonical
    assert CANONICAL_RULE_VERSION == "alias-canonical-v1"

    if isinstance(raw, str) and raw.strip():
        alias = Alias(book=SimpleNamespace(id="synthetic-book"))
        alias.name = raw
        alias._prepare_registration()
        planned, flagged = backfill.plan_backfill([
            backfill.AliasRecord("synthetic-alias", {"name": raw})])
        assert flagged == []
        assert alias.name == raw
        assert alias.canonical_form.encode("utf-8") == canonical.encode("utf-8")
        assert planned[0].updates["canonical_form"] == alias.canonical_form
        assert planned[0].updates["canonical_rule_version"] == alias.canonical_rule_version


def test_harness_uses_app_owned_lexical_rules():
    assert app.NORMALIZATION_VERSION == harness.NORMALIZATION_VERSION == "pilot-normalization-v1"
    for name in ("normalize_text", "normalize_name", "tokenize"):
        assert getattr(harness, name) is getattr(app, name)


def test_run_fingerprint_tracks_the_moved_rules():
    from scripts.extraction_eval.runner import code_fingerprint

    assert code_fingerprint()["text_normalization.py"] == hashlib.sha256(
        Path(app.__file__).read_bytes()).hexdigest()
