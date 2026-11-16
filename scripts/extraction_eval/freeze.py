"""Offline producers and read-only replay of the seal and tuning selections."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from text_content.evaluation import EvaluationErrors

from .exposure import EXPOSED_IDS
from .normalization import normalize_text
from .sampling import select_holdout_books
from .snapshot import _digest

SALT, SEED = "autonomy-r5", 20261106
PROTECTED_SEED = 20261105
PROTECTED_QUOTA = {"alias_positive": 27, "alias_zero": 13}
QUOTA = {"alias_positive": 42, "alias_zero": 38}
EXPECTED_FRAME = {"alias_positive": 63, "alias_zero": 58}
EXPECTED_COUNTS = {
    "raw_books": 195, "eligible_groups": 161,
    "alias_positive": 90, "alias_zero": 71,
    "character_positive": 159, "character_rows": 1297, "alias_rows": 178,
}
CHECK_SEED = 20261110
REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTIONS_PATH = Path(__file__).with_name("selections.json")
EXAMPLES_PATH = REPO_ROOT / "text_content/pipeline_examples.json"
ALIAS_NOTE_PATH = REPO_ROOT / "planning/human_alias_behaviour_2026-11-05.md"
SELECTION_COUNTS = {
    "imported80_ids": 80, "protected40_ids": 40, "tune41_ids": 41,
    "exposed34_ids": 34, "check20_ids": 20, "iterate55_ids": 55,
}
SELECTION_FIELDS = (*SELECTION_COUNTS, "example_source_ids",
                    "heldout_author_shared_ids", "heldout_alias_quoted_ids")


def _check_counts(actual, expected):
    differences = [f"{key}: got {actual.get(key)}, expected {value}"
                   for key, value in expected.items() if actual.get(key) != value]
    if differences:
        raise ValueError("Frame mismatch; stopping without trimming: " + "; ".join(differences))


def _holdout_bytes(freeze):
    # Preserve seal_freeze.py's exclusion-file byte hash, including indentation.
    return (json.dumps(freeze["imported80_ids"], ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def build_freeze(manifest):
    """Replay the fixed design using the selector's eligible representatives.

    The zero-size selection obtains the existing inventory/filtering logic before
    checking the frame. No book content is returned or printed.
    """
    inventory = select_holdout_books(manifest, alias_positive=0, alias_zero=0,
                                     seed=PROTECTED_SEED)
    rows = inventory["inventory"]
    _check_counts({
        "raw_books": len(manifest["books"]), "eligible_groups": len(rows),
        **inventory["eligible_counts"],
        "character_positive": sum(bool(row["character_count"]) for row in rows),
        "character_rows": sum(row["character_count"] for row in rows),
        "alias_rows": sum(row["explicit_alias_count"] for row in rows),
    }, EXPECTED_COUNTS)

    protected = select_holdout_books(manifest, **PROTECTED_QUOTA, seed=PROTECTED_SEED)
    protected_ids = set(protected["book_ids"])
    frame = {label: [row for row in rows if row["id"] not in protected_ids
                     and bool(row["explicit_alias_count"]) == (label == "alias_positive")]
             for label in QUOTA}
    frame_counts = {label: len(population) for label, population in frame.items()}
    _check_counts(frame_counts, EXPECTED_FRAME)

    def order_key(label, work_id):
        return (hashlib.sha256(f"{SALT}|{SEED}|{label}|{work_id}".encode("utf-8")).hexdigest(),
                work_id.encode("utf-8"))

    selected = {}
    for label, quota in QUOTA.items():
        if not 0 <= quota <= len(frame[label]):
            raise ValueError(f"Invalid quota for {label}: {quota}")
        population = sorted(frame[label], key=lambda row: order_key(label, row["id"]))
        selected[label] = sorted(row["id"] for row in population[:quota])
    held_ids = {work_id for ids in selected.values() for work_id in ids}
    tune_ids = {row["id"] for row in rows} - protected_ids - held_ids
    freeze = {
        "version": "autonomy-freeze-v1",
        "design": "planning/autonomy_validation_design_2026-11-06.md §1",
        "snapshot_id": manifest["snapshot_id"],
        "selector": {
            "imported80": {"method": "sha256_order", "salt": SALT, "seed": SEED,
                           "string": "<salt>|<seed>|<stratum>|<stable_work_id>",
                           "tie_break": "bytewise work id"},
            "protected40": {"method": "random.Random.sample", "seed": PROTECTED_SEED,
                            "source": "scripts/extraction_eval/sampling.py::select_holdout_books"},
        },
        "frame_counts": frame_counts, "quota": dict(QUOTA),
        "inclusion_probability": {label: QUOTA[label] / frame_counts[label] for label in QUOTA},
        "imported80_ids": sorted(held_ids), "imported80_by_stratum": selected,
        "protected40_ids": sorted(protected_ids), "tune41_ids": sorted(tune_ids),
        "tune81_ids": sorted(tune_ids | protected_ids),
        # Same canonical JSON digest used by snapshot.py and tuning's loader.
        "imported80_ids_sha256": _digest(sorted(held_ids)),
    }
    freeze["holdout_ids_file_sha256"] = hashlib.sha256(_holdout_bytes(freeze)).hexdigest()
    return freeze


def write_freeze(manifest, out):
    """Write both artifacts once; an existing seal is never replaced."""
    out = Path(out)
    for name in ("freeze.json", "holdout_ids.json"):
        if (out / name).exists():
            raise FileExistsError(f"Refusing existing {name}; choose a new output directory")
    freeze = {**build_freeze(manifest),
              "completed_at": datetime.now(timezone.utc).isoformat(),
              "python": sys.version.split()[0]}
    out.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents replacement if another writer intervenes.
    # Do not use the shared atomic JSON writers: they replace existing files.
    with (out / "freeze.json").open("x", encoding="utf-8") as seal:
        with (out / "holdout_ids.json").open("xb") as ids:
            ids.write(_holdout_bytes(freeze))
        json.dump(freeze, seal, indent=1, sort_keys=True)
        seal.write("\n")
    return freeze


def verify_freeze(manifest, seal_path):
    """Return replay and differing field names, without writing any files.

    Creation time and Python version describe the original run, not the draw.
    All other fields, plus the sibling exclusion file's bytes, must match.
    """
    replay = build_freeze(manifest)
    seal_path = Path(seal_path)
    sealed = json.loads(seal_path.read_text(encoding="utf-8"))
    if not isinstance(sealed, dict):
        raise ValueError("freeze.json must contain an object")
    fields = (replay.keys() | sealed.keys()) - {"completed_at", "python"}
    differences = [key for key in sorted(fields) if replay.get(key) != sealed.get(key)]
    ids_path = seal_path.with_name("holdout_ids.json")
    if not ids_path.is_file() or ids_path.read_bytes() != _holdout_bytes(replay):
        differences.append("holdout_ids.json bytes")
    return replay, differences


def _validate_selections(selections):
    """Catch accidental cohort edits before a harness can use the ids."""
    if not isinstance(selections, dict):
        raise ValueError("Selections must contain an object")
    sets = {}
    for field in SELECTION_FIELDS:
        ids = selections.get(field)
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
            raise ValueError(f"{field} must be a list of nonempty ids")
        sets[field] = set(ids)
        if len(ids) != len(sets[field]):
            raise ValueError(f"{field} contains duplicate ids")
        if field in SELECTION_COUNTS and len(ids) != SELECTION_COUNTS[field]:
            raise ValueError(f"{field}: expected {SELECTION_COUNTS[field]} ids, got {len(ids)}")
    if selections.get("counts") != {field: len(sets[field]) for field in SELECTION_FIELDS}:
        raise ValueError("Selections counts do not match the lists")
    base = ("imported80_ids", "protected40_ids", "tune41_ids", "exposed34_ids")
    for index, left in enumerate(base):
        for right in base[index + 1:]:
            if sets[left] & sets[right]:
                raise ValueError(f"Selections overlap: {left} and {right}")
    check, iterate = sets["check20_ids"], sets["iterate55_ids"]
    if check & iterate:
        raise ValueError("Selections overlap: check20_ids and iterate55_ids")
    if not check <= sets["tune41_ids"]:
        raise ValueError("check20_ids must be a subset of tune41_ids")
    if iterate != sets["exposed34_ids"] | (sets["tune41_ids"] - check):
        raise ValueError("iterate55_ids must equal exposed34_ids plus tune41_ids minus check20_ids")
    if not sets["example_source_ids"] <= iterate:
        raise ValueError("example_source_ids must be a subset of iterate55_ids")
    for field in ("heldout_author_shared_ids", "heldout_alias_quoted_ids"):
        if not sets[field] <= sets["imported80_ids"]:
            raise ValueError(f"{field} must be a subset of imported80_ids")
    if selections.get("imported80_ids_sha256") != _digest(sorted(sets["imported80_ids"])):
        raise ValueError("imported80_ids_sha256 does not match imported80_ids")
    strata = selections.get('book_strata', {})
    if (set(strata) != set().union(*(sets[field] for field in base))
            or any(label not in QUOTA for label in strata.values())):
        raise ValueError(EvaluationErrors.invalid_book_strata)
    return selections


def load_selections(path=SELECTIONS_PATH):
    """Load fixed ids without reading the sealed snapshot or any book content."""
    return _validate_selections(json.loads(Path(path).read_text(encoding="utf-8")))


def alias_counts_from_manifest(path):
    """Read ids and count alias array entries without decoding book content."""
    from .json_sources import JsonSources
    counts = {}
    with JsonSources(path) as source:
        top = source.members(source.root)
        for span in source.items(top['books']):
            book = source.members(span)
            book_id = source.decode(book['id'])
            reference = source.members(book['reference'])
            characters = source.members(reference['characters'])
            count = 0
            for character in source.items(characters['value']):
                fields = source.members(character)
                if 'aliases' in fields:
                    count += len(source.items(fields['aliases']))
            counts[book_id] = count
    return counts


def selection_strata(alias_counts, seal):
    """Use the seal's recorded-alias definition and its fixed proof allocation."""
    ids = set(seal['imported80_ids']) | set(seal['protected40_ids']) | set(seal['tune41_ids']) | EXPOSED_IDS
    result = {i: 'alias_positive' if alias_counts[i] else 'alias_zero' for i in sorted(ids)}
    for label, book_ids in seal['imported80_by_stratum'].items():
        if any(result[i] != label for i in book_ids):
            raise ValueError(EvaluationErrors.changed_alias_strata)
        result.update(dict.fromkeys(book_ids, label))
    return result


def build_selections(manifest, seal, example_pack, alias_note):
    """Produce ids and rules only; the CLI first verifies the supplied seal."""
    books = {book["id"]: book for book in manifest["books"]}
    held = set(seal["imported80_ids"])
    tune = set(seal["tune41_ids"])
    required = held | tune | set(seal["protected40_ids"]) | EXPOSED_IDS
    if not required <= books.keys():
        raise ValueError("Selection ids are missing from the manifest")
    if manifest["snapshot_id"] != seal["snapshot_id"]:
        raise ValueError("Selection snapshot does not match the seal")

    def aliases(book):
        return [alias for character in book["reference"]["characters"]["value"]
                for alias in (character.get("aliases") or [])]

    check = set()
    for label in ("alias_positive", "alias_zero"):
        population = [i for i in tune if bool(aliases(books[i])) == (label == "alias_positive")]
        if len(population) < 10:
            raise ValueError(f"Check draw needs at least ten Tune41 ids in {label}")
        population.sort(key=lambda i: (
            hashlib.sha256(f"tuning-check|{CHECK_SEED}|{label}|{i}".encode("utf-8")).digest(),
            i.encode("utf-8")))
        check.update(population[:10])

    def authors(book):
        values = book["reference"]["metadata"]["authors"]["value"]
        return {normalize_text(value) for value in values if normalize_text(value)}

    outside_authors = {author for i, book in books.items() if i not in held
                       for author in authors(book)}
    # Backticks can span line breaks in the note. Match complete quoted surfaces,
    # not prose substrings or just the books explicitly named in the note.
    quoted = {normalize_text(value) for value in re.findall(r"`([^`]+)`", alias_note)}
    result = {
        "version": "tuning-selections-v1",
        "book_strata": selection_strata({i: len(aliases(book)) for i, book in books.items()}, seal),
        **{field: list(seal[field]) for field in ("imported80_ids", "protected40_ids", "tune41_ids")},
        "imported80_ids_sha256": seal["imported80_ids_sha256"],
        "exposed34_ids": sorted(EXPOSED_IDS),
        "check20_ids": sorted(check),
        "iterate55_ids": sorted(EXPOSED_IDS | (tune - check)),
        "example_source_ids": sorted({example["book_id"] for example in example_pack["examples"]}),
        "heldout_author_shared_ids": sorted(i for i in held if authors(books[i]) & outside_authors),
        "heldout_alias_quoted_ids": sorted(i for i in held
                                           if any(normalize_text(a) in quoted for a in aliases(books[i]))),
        "rules": {
            "imported80_ids": {**seal["selector"]["imported80"], "quota": dict(QUOTA)},
            "protected40_ids": {**seal["selector"]["protected40"], "quota": dict(PROTECTED_QUOTA)},
            "tune41_ids": {"rule": "Sealed eligible representatives minus imported80 and protected40",
                           "seed": None},
            "exposed34_ids": {"rule": "scripts/extraction_eval/exposure.py::EXPOSED_IDS", "seed": None},
            "check20_ids": {"rule": "First ten Tune41 ids per alias stratum by ascending SHA-256",
                            "string": "tuning-check|20261110|<stratum>|<id>",
                            "strata": ["alias_positive", "alias_zero"],
                            "stratum_rule": "Presence of recorded character aliases in the sealed manifest",
                            "seed": CHECK_SEED, "tie_break": "bytewise UTF-8 id",
                            "quota": {"alias_positive": 10, "alias_zero": 10}},
            "iterate55_ids": {"rule": "exposed34_ids union (tune41_ids minus check20_ids)", "seed": None},
            "example_source_ids": {"rule": "Unique examples[].book_id in text_content/pipeline_examples.json",
                                   "seed": None},
            "heldout_author_shared_ids": {
                "rule": "Held-out ids sharing any nonempty recorded metadata.authors.value name with any "
                        "manifest book outside imported80; compare whole names using normalize_text "
                        "(NFKC, casefold, canonical apostrophes, collapsed whitespace); "
                        "do not split combined names or infer missing authors",
                "seed": None},
            "heldout_alias_quoted_ids": {
                "rule": "Held-out ids with any recorded character alias equal to a complete backtick span "
                        "(including multiline spans) in planning/human_alias_behaviour_2026-11-05.md; "
                        "compare using normalize_text (NFKC, casefold, canonical apostrophes, collapsed "
                        "whitespace), retaining articles and punctuation; no substring matching",
                "seed": None},
        },
    }
    result["counts"] = {field: len(result[field]) for field in SELECTION_FIELDS}
    return _validate_selections(result)


def _selections_bytes(selections):
    return (json.dumps(selections, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write", help="Create a new seal from an existing snapshot manifest")
    write.add_argument("--manifest", required=True, type=Path)
    write.add_argument("--out", required=True, type=Path)
    verify = commands.add_parser("verify", help="Replay the draws and check a seal read-only")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--freeze", required=True, type=Path)
    verify.add_argument("--selections", nargs="?", const=SELECTIONS_PATH, type=Path,
                        help="Also compare selections against regeneration, byte for byte")
    selections = commands.add_parser("selections", help="Generate the ids-only tuning selections")
    selections.add_argument("--manifest", required=True, type=Path)
    selections.add_argument("--freeze", required=True, type=Path)
    selections.add_argument("--out", default=SELECTIONS_PATH, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.command == "write":
            freeze, differences = write_freeze(manifest, args.out), []
        else:
            freeze, differences = verify_freeze(manifest, args.freeze)
        if (args.command == "selections" or
                (args.command == "verify" and args.selections is not None)):
            if differences:
                raise ValueError("Seal replay differs: " + ", ".join(differences))
            selected = build_selections(
                manifest, freeze, json.loads(EXAMPLES_PATH.read_text(encoding="utf-8")),
                ALIAS_NOTE_PATH.read_text(encoding="utf-8"))
            data = _selections_bytes(selected)
            if args.command == "selections":
                args.out.write_bytes(data)
            elif args.selections.read_bytes() != data:
                differences.append("selections.json bytes")
            for field, count in selected["counts"].items():
                print(f"{field}: {count}")
    except (OSError, ValueError) as exc:
        print(f"Freeze failed: {exc}", file=sys.stderr)
        return 1
    for label, field in (("Protected", "protected40_ids"), ("Imported", "imported80_ids"),
                         ("Tune41", "tune41_ids"), ("Tune81", "tune81_ids")):
        print(f"{label}: {len(freeze[field])}")
    print(f"Imported id-set SHA-256: {freeze['imported80_ids_sha256']}")
    for field in differences:
        print(f"Difference: {field}")
    print("Verification FAILED" if differences else
          "Verification passed (read-only)" if args.command == "verify" else
          "Selections written" if args.command == "selections" else "Freeze written")
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
