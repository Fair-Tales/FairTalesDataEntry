"""Random sampling of raw page images across many books for rotation-prompt
evaluation (#217 follow-up, 2026-07-18). READ-ONLY on S3.

Reproduces the labelled sample in ``sample_manifest.json`` / ``rot_labels.json``:
main batch seed 20260718 (14 books x 4 pages) + supplement seed 20260719
(4 books x 4 pages) = 72 images from 18 books.

Downloads each sampled RAW ``page_N.jpg`` into ``<workdir>/sample/`` and writes
a preview copy through ``image_processing.downscale_for_vision`` (max_edge=1568,
q85) — EXACTLY the bytes the production pipeline sends to the vision model — so
ground-truth labelling (by viewing the preview) and evaluation use the same
pixels.

Runs OUTSIDE Streamlit (documented ``scripts/`` exception): clients are built
directly from ``.streamlit/secrets.toml``.

Usage (from the repo root):
    PYTHONPATH=. .venv/bin/python scripts/rotation_prompt_eval/rot_sample.py
Workdir defaults to ``scripts/rotation_prompt_eval/work``; override with
``ROT_EVAL_WORKDIR``.
"""
import json
import os
import random
import sys
import tomllib

import s3fs

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
from image_processing import downscale_for_vision  # noqa: E402
from s3_constants import (  # noqa: E402
    S3_BUCKET, NON_BOOK_S3_PREFIXES, is_page_image, page_image_number,
)

WORKDIR = os.environ.get(
    "ROT_EVAL_WORKDIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "work")
)
OUT = os.path.join(WORKDIR, "sample")
os.makedirs(OUT, exist_ok=True)

MAIN_SEED, MAIN_BOOKS, PAGES_PER_BOOK, TARGET = 20260718, 14, 4, 55
SUPPLEMENT_SEED, SUPPLEMENT_BOOKS = 20260719, 4

with open(os.path.join(REPO, ".streamlit/secrets.toml"), "rb") as fh:
    secrets = tomllib.load(fh)
fs = s3fs.S3FileSystem(key=secrets["AWS_ACCESS_KEY_ID"],
                       secret=secrets["AWS_SECRET_ACCESS_KEY"])


def list_book_pages():
    folders = sorted(
        f for f in fs.ls(S3_BUCKET, detail=False)
        if f.rsplit("/", 1)[-1] not in NON_BOOK_S3_PREFIXES
    )
    book_pages = {}
    for folder in folders:
        try:
            entries = fs.ls(folder, detail=False)
        except FileNotFoundError:
            continue
        pages = sorted((page_image_number(e), e) for e in entries if is_page_image(e))
        if pages:
            book_pages[folder] = pages
    return book_pages


def download(folder, n, key):
    book = folder.rsplit("/", 1)[-1]
    local_id = f"{book.replace(' ', '_').replace('/', '_')}__page_{n}"
    raw_path = os.path.join(OUT, local_id + ".jpg")
    if not os.path.exists(raw_path):
        with fs.open(key, "rb") as h:
            data = h.read()
        with open(raw_path, "wb") as f:
            f.write(data)
        with open(os.path.join(OUT, local_id + "_preview.jpg"), "wb") as f:
            f.write(downscale_for_vision(data))
    print(f"  {local_id}")
    return {"id": local_id, "s3_key": key, "book": book, "page": n}


def main():
    book_pages = list_book_pages()
    print(f"{len(book_pages)} books with raw pages")

    # Main batch (seed 20260718).
    rng = random.Random(MAIN_SEED)
    books = rng.sample(sorted(book_pages), min(MAIN_BOOKS, len(book_pages)))
    sample = []
    for folder in books:
        take = rng.sample(book_pages[folder], min(PAGES_PER_BOOK, len(book_pages[folder])))
        sample.extend((folder, n, key) for n, key in take)
    while len(sample) < TARGET:
        extra = rng.choice(sorted(set(book_pages) - set(books)))
        books.append(extra)
        take = rng.sample(book_pages[extra], min(PAGES_PER_BOOK, len(book_pages[extra])))
        sample.extend((extra, n, key) for n, key in take)

    # Supplement (seed 20260719): 4 more books not in the main batch.
    rng2 = random.Random(SUPPLEMENT_SEED)
    extra_books = rng2.sample(
        sorted(set(book_pages) - set(books)), SUPPLEMENT_BOOKS
    )
    for folder in extra_books:
        take = rng2.sample(book_pages[folder], min(PAGES_PER_BOOK, len(book_pages[folder])))
        sample.extend((folder, n, key) for n, key in take)
    books += extra_books

    manifest = {
        "seed": MAIN_SEED, "supplement_seed": SUPPLEMENT_SEED,
        "n_books": len(books), "books": books,
        "images": [download(folder, n, key) for folder, n, key in sample],
    }
    with open(os.path.join(WORKDIR, "sample_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"\n{len(manifest['images'])} images from {len(books)} books -> {OUT}")


if __name__ == "__main__":
    main()
