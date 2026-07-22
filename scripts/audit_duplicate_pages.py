"""READ-ONLY audit: find book pages with byte-identical content in S3.

Context: a flaky-WiFi upload bug caused some books to store the same photo
under two page slots (e.g. page_24.jpg byte-identical to page_1.jpg). This
script scans every book folder under the S3 bucket and, within each book,
groups canonical ``page_N.jpg`` images that share content, then classifies
the likely cause per book:

  * ``append-on-reselect`` - the first pages re-appear at the end with a
    constant offset (e.g. page_1==page_24, page_2==page_25): the upload bug.
  * ``endpaper-reuse-candidate`` - every duplicated page sits in the front or
    back block of the book and at least one group spans front->back (e.g.
    page_2==page_35, or page_2==page_4==page_32==page_34): the same photo
    deliberately re-used for the (identical) front and back endpapers.
    Possibly intentional, flag for human judgement.
  * ``other/interleaved`` - anything else (mid-book repeats, adjacent-slot
    double uploads, blank-page photo reuse, ...).

Content comparison uses S3 ETags (for single-part uploads the ETag IS the
MD5 of the object, so identical content <=> identical ETag) read from the
object listings - NO image bytes are downloaded in the normal case. The only
time bytes are fetched is the rare fallback where a book contains two or
more multipart-uploaded objects (ETag containing '-') of the same size,
whose ETags cannot be compared reliably; those few objects are then hashed.

This script runs OUTSIDE Streamlit (the documented ``scripts/data_cleanup.py``
exception, #129): it loads ``.streamlit/secrets.toml`` directly and builds its
own ``s3fs.S3FileSystem``. It NEVER writes, deletes, or modifies any S3 object
or Firestore document - it only lists objects and reads their metadata.

Usage (from the project root):

    PYTHONPATH=. .venv/bin/python scripts/audit_duplicate_pages.py

Writes a machine-readable summary to
``scripts/audit_duplicate_pages_report.json`` (local file only) and prints a
human-readable report to stdout.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict

from s3_constants import (
    NON_BOOK_S3_PREFIXES,
    S3_BUCKET,
    is_page_image,
    page_image_number,
)

#: Default path to the Streamlit secrets file (same as scripts/data_cleanup.py).
DEFAULT_SECRETS = ".streamlit/secrets.toml"

#: Default path for the machine-readable report.
DEFAULT_REPORT = os.path.join(
    os.path.dirname(__file__), "audit_duplicate_pages_report.json"
)


# ---------------------------------------------------------------------------
# Secrets + filesystem construction (data_cleanup.py pattern - runs outside
# Streamlit, so no st.secrets / utilities.get_s3_filesystem()).
# ---------------------------------------------------------------------------


def load_secrets(path: str) -> dict:
    """Load ``.streamlit/secrets.toml`` directly (no Streamlit dependency)."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"secrets file not found: {path} (run from the project root, or pass "
            "--secrets). This tool must run where the real secrets exist."
        )
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def make_filesystem(secrets: dict):
    """Build the read-only s3fs filesystem from AWS keys in secrets."""
    import s3fs

    for required in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if required not in secrets:
            raise KeyError(f"secrets is missing required '{required}'")
    return s3fs.S3FileSystem(
        anon=False,
        key=secrets["AWS_ACCESS_KEY_ID"],
        secret=secrets["AWS_SECRET_ACCESS_KEY"],
    )


# ---------------------------------------------------------------------------
# Pure helpers (no S3 access) - content grouping + classification.
# ---------------------------------------------------------------------------


def normalise_etag(etag: str) -> str:
    """Strip the surrounding double quotes S3 puts around ETag values."""
    return (etag or "").strip('"')


def is_multipart_etag(etag: str) -> bool:
    """Multipart-upload ETags contain a '-'; they are NOT a plain content MD5."""
    return "-" in etag


def group_pages_by_content(pages: list[dict], hash_bytes) -> list[dict]:
    """Group a book's page entries by content identity.

    ``pages`` is a list of ``{"number", "name", "etag", "size"}`` dicts.
    ``hash_bytes(name) -> str`` is called ONLY for the multipart-ETag fallback.

    Returns duplicate groups (>= 2 pages sharing content), each as
    ``{"pages": [int, ...], "etag": str, "content_key": str}`` sorted by the
    lowest page number in the group.
    """
    # Primary bucket: single-part ETags are content MDSums - group directly.
    by_key: dict[str, list[dict]] = defaultdict(list)
    multipart: list[dict] = []
    for page in pages:
        if is_multipart_etag(page["etag"]):
            multipart.append(page)
        else:
            by_key[f"md5:{page['etag']}"].append(page)

    # Fallback: multipart ETags are not comparable content hashes. Identical
    # multipart ETag + size => identical content (same parts), so group those
    # directly; otherwise, only when two+ multipart objects share a SIZE could
    # they still be hidden duplicates - hash exactly those objects' bytes.
    mp_by_etag_size: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for page in multipart:
        mp_by_etag_size[(page["etag"], page["size"])].append(page)
    ambiguous_by_size: dict[int, list[dict]] = defaultdict(list)
    for (etag, size), group in mp_by_etag_size.items():
        if len(group) > 1:
            by_key[f"mp-etag:{etag}/{size}"].extend(group)
        else:
            ambiguous_by_size[size].append(group[0])
    for size, group in ambiguous_by_size.items():
        if len(group) < 2:
            continue  # unique size => cannot be a duplicate of anything here
        for page in group:
            digest = hash_bytes(page["name"])
            by_key[f"sha256:{digest}"].append(page)

    groups = []
    for key, members in by_key.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda p: p["number"])
        groups.append(
            {
                "pages": [p["number"] for p in members],
                "etag": members[0]["etag"],
                "content_key": key,
            }
        )
    groups.sort(key=lambda g: g["pages"][0])
    return groups


def classify(groups: list[dict], max_page: int) -> str:
    """Classify a book's duplicate-group pattern (see module docstring)."""
    pair_groups = [g for g in groups if len(g["pages"]) == 2]
    if len(pair_groups) == len(groups) and len(groups) >= 2:
        offsets = {g["pages"][1] - g["pages"][0] for g in groups}
        lows = sorted(g["pages"][0] for g in groups)
        highs = sorted(g["pages"][1] for g in groups)
        consecutive = all(b - a == 1 for a, b in zip(lows, lows[1:]))
        if (
            len(offsets) == 1
            and consecutive
            and highs[-1] == max_page  # the re-appended run ends at the last page
        ):
            return "append-on-reselect"
    # Endpaper reuse: picture books typically print IDENTICAL front and back
    # endpapers, and byte-identical files mean the same uploaded photo was
    # used for both. Signature: every duplicated page lies in the front or
    # back block of the book, and at least one group spans front -> back.
    block = max(3, max_page // 5)

    def in_front(p: int) -> bool:
        return p <= block

    def in_back(p: int) -> bool:
        return p >= max_page - block + 1

    if groups and all(in_front(p) or in_back(p) for g in groups for p in g["pages"]):
        if any(
            any(in_front(p) for p in g["pages"]) and any(in_back(p) for p in g["pages"])
            for g in groups
        ):
            return "endpaper-reuse-candidate"
    return "other/interleaved"


# ---------------------------------------------------------------------------
# S3 scan (list-only).
# ---------------------------------------------------------------------------


def list_book_folders(fs) -> list[str]:
    """Book folder names: immediate children of the bucket, minus non-book prefixes."""
    folders = []
    for entry in fs.ls(S3_BUCKET, detail=True):
        if entry.get("type") != "directory":
            continue
        name = entry["name"].rstrip("/").split("/")[-1]
        if name in NON_BOOK_S3_PREFIXES:
            continue
        folders.append(name)
    return sorted(folders)


def scan_book(fs, folder: str) -> dict:
    """Audit one book folder; returns its duplicate groups + classification."""
    entries = fs.ls(f"{S3_BUCKET}/{folder}", detail=True)
    canonical: list[dict] = []
    derivatives: list[dict] = []
    for entry in entries:
        if entry.get("type") == "directory":
            continue
        name = entry["name"]
        basename = os.path.basename(name)
        record = {
            "name": name,
            "etag": normalise_etag(entry.get("ETag", "")),
            "size": entry.get("size", entry.get("Size", 0)),
        }
        if is_page_image(name):
            record["number"] = page_image_number(name)
            canonical.append(record)
        elif basename.lower().endswith((".jpg", ".jpeg", ".png")):
            record["basename"] = basename
            derivatives.append(record)

    def hash_bytes(path: str) -> str:
        print(f"    [fallback] hashing multipart object {path}", file=sys.stderr)
        return hashlib.sha256(fs.cat_file(path)).hexdigest()

    groups = group_pages_by_content(canonical, hash_bytes)
    max_page = max((p["number"] for p in canonical), default=0)

    # Trivial side note: derivative (_cropped/_display) files sharing content.
    deriv_by_etag: dict[str, list[str]] = defaultdict(list)
    for record in derivatives:
        if record["etag"] and not is_multipart_etag(record["etag"]):
            deriv_by_etag[record["etag"]].append(record["basename"])
    duplicate_derivatives = sorted(
        (sorted(names) for names in deriv_by_etag.values() if len(names) > 1),
        key=lambda names: names[0],
    )

    return {
        "folder": folder,
        "page_count": len(canonical),
        "max_page": max_page,
        "duplicate_groups": groups,
        "classification": classify(groups, max_page) if groups else None,
        "duplicate_derivatives": duplicate_derivatives,
    }


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------


def print_report(results: list[dict]) -> None:
    affected = [r for r in results if r["duplicate_groups"]]
    print("=" * 72)
    print("DUPLICATE-PAGE CONTENT AUDIT (read-only, ETag-based)")
    print("=" * 72)
    print(f"Books scanned:  {len(results)}")
    print(f"Books affected: {len(affected)}")
    print()
    for result in affected:
        print(
            f"- {result['folder']}  "
            f"({result['page_count']} pages, max page {result['max_page']})"
        )
        print(f"    classification: {result['classification']}")
        for group in result["duplicate_groups"]:
            pages = " == ".join(f"page_{n}" for n in group["pages"])
            print(f"    {pages}   (ETag {group['etag']})")
        if result["duplicate_derivatives"]:
            print(
                f"    note: {len(result['duplicate_derivatives'])} duplicate "
                f"derivative group(s): "
                + "; ".join(" == ".join(g) for g in result["duplicate_derivatives"])
            )
        print()
    if not affected:
        print("No books with byte-identical page images found.")
    by_class: dict[str, int] = defaultdict(int)
    for result in affected:
        by_class[result["classification"]] += 1
    if by_class:
        print("By classification:")
        for name, count in sorted(by_class.items()):
            print(f"  {name}: {count}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--secrets",
        default=DEFAULT_SECRETS,
        help=f"Path to secrets.toml (default: {DEFAULT_SECRETS}).",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"Path for the JSON report (default: {DEFAULT_REPORT}).",
    )
    args = parser.parse_args(argv)

    fs = make_filesystem(load_secrets(args.secrets))
    folders = list_book_folders(fs)
    print(
        f"Scanning {len(folders)} book folders under s3://{S3_BUCKET} ...",
        file=sys.stderr,
    )

    results = []
    for folder in folders:
        try:
            results.append(scan_book(fs, folder))
        except FileNotFoundError as exc:
            # Folder vanished between the top-level listing and its scan.
            print(
                f"  WARNING: {folder}: listing failed ({exc}); skipped", file=sys.stderr
            )

    print_report(results)

    affected = [r for r in results if r["duplicate_groups"]]
    report = {
        "bucket": S3_BUCKET,
        "books_scanned": len(results),
        "books_affected": len(affected),
        "affected": affected,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nJSON report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
