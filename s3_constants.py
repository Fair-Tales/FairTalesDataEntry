"""Pure S3 path constants + helpers shared by the app and the cleanup CLI (#129).

This module deliberately imports NOTHING from Streamlit (or google / s3fs at
import time), so it can be imported equally by:

  * ``book_reconstruction.py`` (runs inside Streamlit), and
  * ``scripts/data_cleanup.py`` (runs OUTSIDE Streamlit, standalone CLI),

replacing the two drifting copies of these constants/helpers that previously
lived in each. The page-image filename rule and the book-folder-name derivation
MUST match between the live app and the cleanup tooling, otherwise the cleanup
CLI would mis-classify real book folders as orphans (or vice versa); keeping a
single definition here is what guarantees they stay in step.
"""

import os
import re

#: S3 bucket holding book page images (first path segment, app-wide).
S3_BUCKET = "sawimages"

#: Immediate child prefixes under the bucket that are NOT book folders and so
#: must never be reported as "orphaned" (e.g. the transient direct-upload area
#: ``uploads/{flow}/{session}/`` written by photo_upload.py / uploader.py).
NON_BOOK_S3_PREFIXES = ("uploads",)

_PAGE_IMAGE_RE = re.compile(r"^page_(\d+)\.jpg$", re.IGNORECASE)


def is_page_image(path: str) -> bool:
    """True for ``page_N.jpg`` originals; False for ``page_N_cropped.jpg`` and
    anything else.

    Page images are the canonical originals; ``_cropped`` variants are derived
    and so are ignored when listing / counting a book's pages. Accepts either a
    bare filename or a full S3 path (the basename is matched).
    """
    return bool(_PAGE_IMAGE_RE.match(os.path.basename(path)))


def page_image_number(path: str):
    """Page number of a ``page_N.jpg`` original, or ``None`` for anything else
    (including the derived ``_cropped``/``_display`` variants)."""
    match = _PAGE_IMAGE_RE.match(os.path.basename(path))
    return int(match.group(1)) if match else None


def book_folder_name(title: str, photos_url: str = "") -> str:
    """The S3 folder *name* (segment under the bucket) for a book.

    The app writes pages to ``sawimages/{title}`` (see uploader.py), storing
    that path in ``photos_url``. Prefer the stored ``photos_url`` basename; fall
    back to the raw title.
    """
    source = photos_url.strip() if photos_url else ""
    if not source:
        source = title or ""
    return source.rstrip("/").split("/")[-1]


def count_folder_pages(fs, folder: str) -> int:
    """Number of ``page_N.jpg`` (non-cropped) objects under ``{S3_BUCKET}/{folder}``.

    Operates directly on an s3fs filesystem (used by the live app's
    book_reconstruction; the cleanup CLI counts via its backend abstraction).
    """
    prefix = f"{S3_BUCKET}/{folder}"
    try:
        if not fs.exists(prefix):
            return 0
        return sum(1 for path in fs.find(prefix) if is_page_image(path))
    except FileNotFoundError:
        return 0


def max_folder_page(fs, folder: str) -> int:
    """Highest ``page_N.jpg`` page number under ``{S3_BUCKET}/{folder}`` (0 when
    the folder is missing or holds no page images).

    Used by the append-photos flow (#203) to compute where new pages start:
    unlike :func:`count_folder_pages` it is robust to numbering holes, so an
    appended page can NEVER be assigned an existing page's number/filename.
    """
    prefix = f"{S3_BUCKET}/{folder}"
    try:
        if not fs.exists(prefix):
            return 0
        numbers = (page_image_number(path) for path in fs.find(prefix))
        return max((n for n in numbers if n is not None), default=0)
    except FileNotFoundError:
        return 0
