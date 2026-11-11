"""Synthetic checks for the recovered freeze producer; no real book content."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts.extraction_eval import freeze
from scripts.extraction_eval.exposure import EXPOSED_IDS
from scripts.extraction_eval.tuning import TuningLoader


@pytest.fixture
def manifest(monkeypatch):
    books = []
    for label in ("p", "z"):
        for index in range(4):
            work_id = f"{label}-{index}"
            books.append({
                "id": work_id,
                "pages": [{"image": {"sha256": work_id}, "reference": {
                    "text": {"status": "observed", "value": "Synthetic story."},
                    "contains_story": {"value": True},
                }}],
                "reference": {"characters": {"value": [] if work_id == "z-3" else [
                    {"aliases": ["synthetic alias"] if label == "p" else []}
                ]}},
            })
    # Reuse the real selector's duplicate, exposure, image and story exclusions.
    books[0]["holdout_group"] = "synthetic-group"
    duplicate = deepcopy(books[0])
    duplicate["id"] = "zz-duplicate"
    no_image = deepcopy(books[0])
    no_image.update(id="no-image", pages=[])
    exposed = deepcopy(books[1])
    exposed["id"] = sorted(EXPOSED_IDS)[0]
    exposed["pages"][0]["image"]["sha256"] = "exposed-image"
    nonstory = deepcopy(books[1])
    nonstory["id"] = "nonstory"
    nonstory["pages"][0]["image"]["sha256"] = "nonstory-image"
    nonstory["pages"][0]["reference"]["contains_story"]["value"] = False
    books.extend([duplicate, no_image, exposed, nonstory])
    monkeypatch.setattr(freeze, "EXPECTED_COUNTS", {
        "raw_books": 12, "eligible_groups": 8, "alias_positive": 4, "alias_zero": 4,
        "character_positive": 7, "character_rows": 7, "alias_rows": 4,
    })
    monkeypatch.setattr(freeze, "PROTECTED_QUOTA", {"alias_positive": 1, "alias_zero": 1})
    monkeypatch.setattr(freeze, "EXPECTED_FRAME", {"alias_positive": 3, "alias_zero": 3})
    monkeypatch.setattr(freeze, "QUOTA", {"alias_positive": 2, "alias_zero": 2})
    return {"snapshot_id": "synthetic-snapshot", "books": books}


def test_draw_is_deterministic_and_partitions_eligible_groups(manifest):
    result = freeze.build_freeze(manifest)
    shuffled = {**manifest, "books": list(reversed(manifest["books"]))}
    assert result == freeze.build_freeze(shuffled)
    held = set(result["imported80_ids"])
    protected = set(result["protected40_ids"])
    tune = set(result["tune41_ids"])
    assert (len(held), len(protected), len(tune)) == (4, 2, 2)
    assert not (held & protected or held & tune or protected & tune)
    assert held | protected | tune == {f"{label}-{i}" for label in ("p", "z") for i in range(4)}
    assert set(result["tune81_ids"]) == protected | tune
    assert {key: len(ids) for key, ids in result["imported80_by_stratum"].items()} == freeze.QUOTA
    canon = json.dumps(sorted(held), ensure_ascii=False, separators=(",", ":"))
    assert result["imported80_ids_sha256"] == hashlib.sha256(canon.encode()).hexdigest()


@pytest.mark.parametrize("field", list(freeze.EXPECTED_COUNTS))
def test_frame_mismatch_stops_before_drawing_or_writing(manifest, monkeypatch, tmp_path, field):
    monkeypatch.setitem(freeze.EXPECTED_COUNTS, field, freeze.EXPECTED_COUNTS[field] + 1)
    original_selector = freeze.select_holdout_books

    def inventory_only(manifest, **kwargs):
        assert kwargs["alias_positive"] == kwargs["alias_zero"] == 0
        return original_selector(manifest, **kwargs)

    monkeypatch.setattr(freeze, "select_holdout_books", inventory_only)
    with pytest.raises(ValueError, match=f"Frame mismatch.*{field}"):
        freeze.write_freeze(manifest, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_complement_mismatch_stops_without_trimming(manifest, monkeypatch):
    monkeypatch.setitem(freeze.EXPECTED_FRAME, "alias_positive", 2)
    with pytest.raises(ValueError, match="Frame mismatch.*alias_positive: got 3, expected 2"):
        freeze.build_freeze(manifest)


@pytest.mark.parametrize("existing", ["freeze.json", "holdout_ids.json"])
def test_write_refuses_existing_artifact(manifest, tmp_path, existing):
    path = tmp_path / existing
    path.write_bytes(b"original artifact")
    with pytest.raises(FileExistsError):
        freeze.write_freeze(manifest, tmp_path)
    assert path.read_bytes() == b"original artifact"
    assert sorted(p.name for p in tmp_path.iterdir()) == [existing]


def test_verify_own_output_is_read_only_and_tuning_compatible(manifest, tmp_path, monkeypatch):
    out = tmp_path / "seal"
    written = freeze.write_freeze(manifest, out)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()}
    # Verification must not open any path for writing, even if bytes would agree.
    original_open = freeze.Path.open

    def read_only(path, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(freeze.Path, "open", read_only)
        replay, differences = freeze.verify_freeze(manifest, out / "freeze.json")
    assert differences == []
    assert replay["imported80_ids"] == written["imported80_ids"]
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()} == before
    loader = TuningLoader(tmp_path / "run", out)
    assert loader.holdout == set(written["imported80_ids"])


@pytest.mark.parametrize("field", ["protected40_ids", "imported80_ids", "tune41_ids",
                                    "tune81_ids", "imported80_by_stratum", "imported80_ids_sha256"])
def test_verify_detects_changed_seal_fields(manifest, tmp_path, field):
    written = freeze.write_freeze(manifest, tmp_path)
    if isinstance(written[field], list):
        written[field][0] = "altered-id"
    elif isinstance(written[field], dict):
        written[field]["alias_positive"][0] = "altered-id"
    else:
        written[field] = "0" * 64
    seal_path = tmp_path / "freeze.json"
    seal_path.write_text(json.dumps(written))
    _, differences = freeze.verify_freeze(manifest, seal_path)
    assert field in differences


def test_cli_write_verify_and_failure(manifest, tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    out = tmp_path / "seal"
    assert freeze.main(["write", "--manifest", str(manifest_path), "--out", str(out)]) == 0
    args = ["verify", "--manifest", str(manifest_path), "--freeze", str(out / "freeze.json")]
    assert freeze.main(args) == 0
    assert "Verification passed (read-only)" in capsys.readouterr().out
    (out / "holdout_ids.json").write_text('["altered-id"]')
    assert freeze.main(args) == 1
    output = capsys.readouterr().out
    assert "Difference: holdout_ids.json bytes" in output
    assert "Verification FAILED" in output
    assert "altered-id" not in output


@pytest.fixture
def selection_inputs(monkeypatch):
    """Full-sized synthetic frame, with the real draw quotas and exposure ids."""
    books = []
    for book_id in ([f"positive-{i:03}" for i in range(90)]
                    + [f"zero-{i:03}" for i in range(71)] + sorted(EXPOSED_IDS)):
        books.append({
            "id": book_id,
            "pages": [{"image": {"sha256": book_id}, "reference": {
                "text": {"status": "observed", "value": "Synthetic story."},
                "contains_story": {"value": True},
            }}],
            "reference": {
                "characters": {"value": [{"aliases": [f"alias {book_id}"]
                                          if book_id.startswith("positive-") else []}]},
                "metadata": {"authors": {"value": [f"Synthetic author {book_id}"]}},
            },
        })
    monkeypatch.setattr(freeze, "EXPECTED_COUNTS", {
        **freeze.EXPECTED_COUNTS, "character_positive": 161,
        "character_rows": 161, "alias_rows": 90,
    })
    manifest = {"snapshot_id": "synthetic-selections", "books": books}
    seal = freeze.build_freeze(manifest)
    examples = {"examples": [{"book_id": min(EXPOSED_IDS)}]}
    return manifest, seal, examples, "No quoted aliases."


def test_check_draw_and_partition(selection_inputs):
    manifest, seal, examples, note = selection_inputs
    selected = freeze.build_selections(*selection_inputs)
    shuffled = {**manifest, "books": list(reversed(manifest["books"]))}
    assert selected == freeze.build_selections(shuffled, seal, examples, note)
    expected = set()
    for label, prefix in (("alias_positive", "positive-"), ("alias_zero", "zero-")):
        population = [i for i in seal["tune41_ids"] if i.startswith(prefix)]
        ordered = sorted(population, key=lambda i: (
            hashlib.sha256(f"tuning-check|20261110|{label}|{i}".encode()).hexdigest(), i.encode()))
        expected.update(ordered[:10])
        assert sum(i.startswith(prefix) for i in selected["check20_ids"]) == 10
    check, iterate = set(selected["check20_ids"]), set(selected["iterate55_ids"])
    assert check == expected
    assert not check & iterate
    assert iterate == EXPOSED_IDS | (set(seal["tune41_ids"]) - check)
    assert not (check | iterate) & (set(seal["imported80_ids"]) | set(seal["protected40_ids"]))
    for field in ("imported80_ids", "protected40_ids", "tune41_ids", "imported80_ids_sha256"):
        assert selected[field] == seal[field]


def test_check_hash_ties_use_bytewise_id(selection_inputs, monkeypatch):
    class SameHash:
        def digest(self):
            return b"same hash"

    original = freeze.hashlib.sha256
    monkeypatch.setattr(freeze.hashlib, "sha256", lambda data: SameHash()
                        if data.startswith(b"tuning-check|") else original(data))
    selected = freeze.build_selections(*selection_inputs)
    tune = selection_inputs[1]["tune41_ids"]
    assert selected["check20_ids"] == sorted(
        [i for i in tune if i.startswith("positive-")][:10]
        + [i for i in tune if i.startswith("zero-")][:10])


def test_sensitivities_match_whole_recorded_values(selection_inputs):
    manifest, seal, examples, _ = selection_inputs
    books = {b["id"]: b for b in manifest["books"]}
    held = seal["imported80_by_stratum"]["alias_positive"][:4]
    # A second author matches a protected book: comparison includes all books
    # outside the held-out set, not just the books used for iteration/examples.
    books[held[0]]["reference"]["metadata"]["authors"]["value"] = ["Private synthetic author", " Ｓhared  Author "]
    books[seal["protected40_ids"][0]]["reference"]["metadata"]["authors"]["value"] = ["shared author"]
    for book_id in held[1:3]:
        books[book_id]["reference"]["metadata"]["authors"]["value"] = ["Held-out only"]
    books[held[3]]["reference"]["metadata"]["authors"]["value"] = []
    for book_id, alias in zip(held, [" The Owl’s pal ", "owl's pal", "pal", "the owl's pal!"]):
        books[book_id]["reference"]["characters"]["value"][0]["aliases"] = [alias]
    examples = {"examples": examples["examples"] * 2}
    result = freeze.build_selections(manifest, seal, examples, "Quoted `the owl's\npal`.")
    assert result["heldout_author_shared_ids"] == [held[0]]
    assert result["heldout_alias_quoted_ids"] == [held[0]]
    assert len(result["example_source_ids"]) == 1
    # Public output must never acquire names/aliases from private inputs.
    serialized = freeze._selections_bytes(result).decode()
    assert "Shared" not in serialized and "owl" not in serialized and "Private synthetic author" not in serialized


@pytest.mark.parametrize("fault", ["overlap", "count", "duplicate", "check_iterate", "heldout_check",
                                    "example", "sensitivity", "hash", "recorded_count"])
def test_selection_loader_rejects_bad_lists(selection_inputs, tmp_path, fault):
    selected = freeze.build_selections(*selection_inputs)
    message = {
        "overlap": "overlap", "count": "expected 20", "duplicate": "duplicate",
        "check_iterate": "overlap", "heldout_check": "subset", "example": "subset",
        "sensitivity": "subset", "hash": "sha256", "recorded_count": "counts",
    }[fault]
    if fault == "overlap":
        selected["protected40_ids"][0] = selected["imported80_ids"][0]
    elif fault == "count":
        selected["check20_ids"].pop()
    elif fault == "duplicate":
        selected["check20_ids"][0] = selected["check20_ids"][1]
    elif fault == "check_iterate":
        selected["iterate55_ids"][0] = selected["check20_ids"][0]
    elif fault == "heldout_check":
        selected["check20_ids"][0] = selected["imported80_ids"][0]
    elif fault == "example":
        selected["example_source_ids"] = [selected["check20_ids"][0]]
    elif fault == "sensitivity":
        selected["heldout_alias_quoted_ids"] = [selected["check20_ids"][0]]
        selected["counts"]["heldout_alias_quoted_ids"] = 1
    elif fault == "hash":
        selected["imported80_ids_sha256"] = "0" * 64
    else:
        selected["counts"]["check20_ids"] = 19
    path = tmp_path / "selections.json"
    path.write_text(json.dumps(selected))
    with pytest.raises(ValueError, match=message):
        freeze.load_selections(path)


def test_selections_cli_generate_verify_and_detect_any_difference(selection_inputs, tmp_path, monkeypatch, capsys):
    manifest, _, examples, note = selection_inputs
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    examples_path, note_path = tmp_path / "examples.json", tmp_path / "note.md"
    examples_path.write_text(json.dumps(examples))
    note_path.write_text(note)
    monkeypatch.setattr(freeze, "EXAMPLES_PATH", examples_path)
    monkeypatch.setattr(freeze, "ALIAS_NOTE_PATH", note_path)
    seal_path = tmp_path / "seal"
    freeze.write_freeze(manifest, seal_path)
    original = {p: p.read_bytes() for p in seal_path.iterdir()}
    args = ["--manifest", str(manifest_path), "--freeze", str(seal_path / "freeze.json")]
    path = tmp_path / "selections.json"
    assert freeze.main(["selections", *args, "--out", str(path)]) == 0
    first = path.read_bytes()
    assert freeze.load_selections(path)["counts"] == {
        **freeze.SELECTION_COUNTS, "example_source_ids": 1,
        "heldout_author_shared_ids": 0, "heldout_alias_quoted_ids": 0,
    }
    assert freeze.main(["selections", *args, "--out", str(path)]) == 0
    assert path.read_bytes() == first
    verify = ["verify", *args, "--selections", str(path)]
    assert freeze.main(verify) == 0
    for changed in (first + b"\n", first.replace(b"20261110", b"20261111")):
        path.write_bytes(changed)
        assert freeze.main(verify) == 1
        assert path.read_bytes() == changed
    assert "Difference: selections.json bytes" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in seal_path.iterdir()} == original


def test_committed_selections_match_real_seal(capsys):
    root = freeze.REPO_ROOT.parent / "autonomy-freeze"
    seal_path, manifest_path = root / "freeze.json", root / "snapshot/manifest.json"
    if not seal_path.is_file() or not manifest_path.is_file():
        pytest.skip("Local sealed snapshot is absent")
    # The CLI reports only counts/hash/difference field names, even on failure.
    status = freeze.main(["verify", "--manifest", str(manifest_path), "--freeze", str(seal_path),
                          "--selections", str(freeze.SELECTIONS_PATH)])
    assert status == 0, capsys.readouterr().out
    freeze.load_selections()
