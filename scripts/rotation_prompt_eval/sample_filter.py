"""Offline cohort filter shared by rotation fixture readers and producers.

An image id must match an entry in sample_manifest.json. Its ``book`` is the
original S3 folder name recorded by rot_sample.download; lowercasing it and
replacing spaces with underscores follows Book.document_id. Only exact ids in
selections.json are known. No title guessing or image inspection is used.
"""
import json
import os
from pathlib import Path

from scripts.extraction_eval.freeze import load_selections

HERE = Path(__file__).resolve().parent
WORKDIR = Path(os.environ.get("ROT_EVAL_WORKDIR", HERE / "work"))


class SampleFilter:
    def __init__(self, *, selections=None, manifest=None):
        selections = load_selections() if selections is None else selections
        if manifest is None:
            manifest = json.loads((HERE / "sample_manifest.json").read_text())
        self.books = {row["id"]: row.get("book") for row in manifest["images"]}
        self.held_out = set(selections["imported80_ids"])
        self.protected = set(selections["protected40_ids"])
        self.allowed = set(selections["tune41_ids"]) | set(selections["exposed34_ids"])

    def book_reason(self, book):
        book_id = book.lower().replace(" ", "_") if isinstance(book, str) else None
        if book_id in self.held_out:
            return "held_out"
        if book_id in self.protected:
            return "protected"
        if book_id not in self.allowed:
            return "unmapped"
        return None

    def reason(self, image_id):
        return self.book_reason(self.books.get(image_id))

    def filter_ids(self, image_ids):
        kept = []
        counts = dict(held_out=0, protected=0, unmapped=0)
        for image_id in image_ids:
            reason = self.reason(image_id)
            if reason:
                counts[reason] += 1
            else:
                kept.append(image_id)
        print(f"Rotation samples: kept={len(kept)}, skipped={sum(counts.values())} "
              f"(held_out={counts['held_out']}, protected={counts['protected']}, "
              f"unmapped={counts['unmapped']})")
        return kept


def load_labels():
    labels = json.loads((HERE / "rot_labels.json").read_text())["labels"]
    return {image_id: labels[image_id] for image_id in SampleFilter().filter_ids(labels)}


def load_preview(image_id):
    reason = SampleFilter().reason(image_id)
    if reason:
        raise ValueError(f"Rotation sample refused: {reason}")
    return (WORKDIR / "sample" / (image_id + "_preview.jpg")).read_bytes()
