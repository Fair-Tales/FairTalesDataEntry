"""Cohort filtering uses synthetic metadata only; no images or services."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.rotation_prompt_eval import sample_filter


@pytest.fixture
def samples(tmp_path, monkeypatch):
    selections = {
        "imported80_ids": ["held_book"], "protected40_ids": ["protected_book"],
        "tune41_ids": ["tune_book"], "exposed34_ids": ["exposed_book"],
    }
    # Deliberately opaque image ids: identity comes from the manifest's book.
    manifest = {"images": [
        {"id": "a", "book": "Tune Book"},
        {"id": "b", "book": "Exposed Book"},
        {"id": "c", "book": "Held Book"},
        {"id": "d", "book": "Protected Book"},
        {"id": "e", "book": "Unknown Book"},
        {"id": "f"},
    ]}
    labels = {key: {"angle": 0} for key in ("a", "b", "c", "d", "e", "f", "missing")}
    labels["b"]["excluded"] = True  # Existing scoring exclusion is independent.
    (tmp_path / "sample_manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "rot_labels.json").write_text(json.dumps({"labels": labels}))
    monkeypatch.setattr(sample_filter, "HERE", tmp_path)
    monkeypatch.setattr(sample_filter, "WORKDIR", tmp_path)
    monkeypatch.setattr(sample_filter, "load_selections", lambda: selections)
    return labels


def test_labels_filter_and_counts_only(samples, capsys):
    assert sample_filter.load_labels() == {key: samples[key] for key in ("a", "b")}
    assert capsys.readouterr().out == (
        "Rotation samples: kept=2, skipped=5 (held_out=1, protected=1, unmapped=3)\n")


@pytest.mark.parametrize("image_id,reason", [
    ("c", "held_out"), ("d", "protected"), ("e", "unmapped"),
    ("f", "unmapped"), ("missing", "unmapped"),
    ("Tune_Book__page_1", "unmapped"),  # A plausible filename is not provenance.
])
def test_skipped_preview_fails_before_reading(samples, monkeypatch, image_id, reason):
    monkeypatch.setattr(Path, "read_bytes", lambda path: pytest.fail("Image read attempted"))
    with pytest.raises(ValueError, match=f"^Rotation sample refused: {reason}$"):
        sample_filter.load_preview(image_id)


def test_admitted_preview_uses_expected_path(samples, monkeypatch):
    paths = []
    monkeypatch.setattr(Path, "read_bytes", lambda path: paths.append(path) or b"synthetic")
    assert sample_filter.load_preview("a") == b"synthetic"
    assert paths == [sample_filter.WORKDIR / "sample/a_preview.jpg"]


def script_functions(filename, names, namespace):
    """Use the actual script functions without its top-level service setup."""
    path = Path(__file__).resolve().parents[1] / "scripts/rotation_prompt_eval" / filename
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


def test_sampler_filters_pages_and_refuses_direct_download(samples, capsys):
    from s3_constants import is_page_image, page_image_number

    folders = [f"bucket/{book}" for book in
               ("Tune Book", "Held Book", "Protected Book", "Unknown Book")]
    fs = SimpleNamespace(
        ls=lambda path, **kw: folders if path == "bucket" else [path + "/page_1.jpg"],
        open=lambda *args: pytest.fail("Download attempted"),
    )
    ns = script_functions("rot_sample.py", {"list_book_pages", "download"}, {
        "fs": fs, "S3_BUCKET": "bucket", "NON_BOOK_S3_PREFIXES": (),
        "is_page_image": is_page_image, "page_image_number": page_image_number,
        "SampleFilter": sample_filter.SampleFilter,
    })
    assert list(ns["list_book_pages"]()) == ["bucket/Tune Book"]
    assert capsys.readouterr().out == (
        "Rotation pages skipped: 3 (held_out=1, protected=1, unmapped=1)\n")
    for folder in folders[1:]:
        with pytest.raises(ValueError, match="Rotation sample refused"):
            ns["download"](folder, 1, folder + "/page_1.jpg")


@pytest.mark.parametrize("filename,function", [
    ("rot_eval.py", "eval_synth"), ("rot_spine_probe.py", "eval_pipeline"),
])
def test_fixed_synthetic_bases_are_filtered(samples, filename, function, capsys):
    from collections import defaultdict

    reads = []
    ns = script_functions(filename, {function}, {
        "SampleFilter": sample_filter.SampleFilter,
        "SYNTH_BASES": list(samples), "VARIANTS": {"current": "prompt"},
        "BINARIES": {"current": None}, "phash": lambda prompt: "hash",
        "load_labels": sample_filter.load_labels,
        "load_preview": lambda image_id: reads.append(image_id) or b"synthetic",
        "rotate": lambda image, degrees: image,
        "pipeline": lambda *args: (0, False, "UPRIGHT", None),
        "refined_pipeline": lambda *args: (0, False, "UPRIGHT", None, None),
        "defaultdict": defaultdict,
    })
    if function == "eval_synth":
        ns[function]("current")
        assert reads == ["a", "b"]
    else:
        ns[function]()
        assert reads == ["a", "a", "b"]
    assert "skipped=5 (held_out=1, protected=1, unmapped=3)" in capsys.readouterr().out
