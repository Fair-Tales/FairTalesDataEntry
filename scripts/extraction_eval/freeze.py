"""Offline producer and read-only replay of the 7 November holdout seal."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write", help="Create a new seal from an existing snapshot manifest")
    write.add_argument("--manifest", required=True, type=Path)
    write.add_argument("--out", required=True, type=Path)
    verify = commands.add_parser("verify", help="Replay the draws and check a seal read-only")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--freeze", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.command == "write":
            freeze, differences = write_freeze(manifest, args.out), []
        else:
            freeze, differences = verify_freeze(manifest, args.freeze)
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
          "Verification passed (read-only)" if args.command == "verify" else "Freeze written")
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
