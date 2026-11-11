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
