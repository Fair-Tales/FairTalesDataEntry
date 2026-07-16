"""Unit tests for scripts/backfill_display_copies.py (#78).

Exercise the planning and write logic against an in-memory fake S3 filesystem —
no network, no secrets. The live run against the production bucket is Chris's
(dry-run first), by design.
"""

import io
import os
import sys

import pytest
from PIL import Image

# Make scripts/ importable (it has no __init__.py), matching test_data_cleanup.
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import backfill_display_copies as bd  # noqa: E402


def _jpeg(width=1600, height=2000):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 180, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeFile:
    def __init__(self, fs, path, mode):
        self._fs, self._path, self._mode = fs, path, mode
        if "r" in mode and path not in fs.store:
            raise FileNotFoundError(path)
        self._buf = io.BytesIO(fs.store.get(path, b"") if "r" in mode else b"")

    def read(self):
        return self._buf.getvalue()

    def write(self, data):
        self._fs.store[self._path] = data
        self._fs.writes.append(self._path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeFs:
    """Minimal s3fs stand-in: ``find`` + context-managed ``open``."""

    def __init__(self, store):
        self.store = dict(store)
        self.writes = []

    def find(self, prefix):
        return [p for p in sorted(self.store) if p.startswith(prefix.rstrip("/") + "/")]

    def open(self, path, mode="rb"):
        return FakeFile(self, path, mode)


PAGE = _jpeg()


@pytest.fixture
def fs():
    return FakeFs({
        # Legacy book: no display copies; page 2 has a cropped version.
        "sawimages/Old Book/page_1.jpg": PAGE,
        "sawimages/Old Book/page_2.jpg": PAGE,
        "sawimages/Old Book/page_2_cropped.jpg": PAGE,
        # Post-#184 book: full display coverage — must be untouched.
        "sawimages/New Book/page_1.jpg": PAGE,
        "sawimages/New Book/page_1_display.jpg": b"existing-display",
        # Partial coverage: only page 2 missing.
        "sawimages/Half Book/page_1.jpg": PAGE,
        "sawimages/Half Book/page_1_display.jpg": b"existing-display",
        "sawimages/Half Book/page_2.jpg": PAGE,
        # Non-book prefix (transient upload area) — never touched.
        "sawimages/uploads/flow/session/page_1.jpg": PAGE,
    })


# ---------------------------------------------------------------------------
# Planning.
# ---------------------------------------------------------------------------

def test_plan_finds_only_missing_display_copies(fs):
    items = bd.plan_backfill(fs)
    dests = {i.dest_path for i in items}
    assert dests == {
        "sawimages/Old Book/page_1_display.jpg",
        "sawimages/Old Book/page_2_display.jpg",
        "sawimages/Half Book/page_2_display.jpg",
    }


def test_plan_prefers_cropped_source_when_present(fs):
    items = {i.dest_path: i for i in bd.plan_backfill(fs)}
    # Page 2 has a corrected image — the display copy must be derived from it
    # (that is what the app shows by default), page 1 from the raw original.
    assert items["sawimages/Old Book/page_2_display.jpg"].source_path == (
        "sawimages/Old Book/page_2_cropped.jpg"
    )
    assert items["sawimages/Old Book/page_1_display.jpg"].source_path == (
        "sawimages/Old Book/page_1.jpg"
    )


def test_plan_skips_upload_prefix(fs):
    assert not any(i.folder == "uploads" for i in bd.plan_backfill(fs))


def test_plan_folder_filter(fs):
    items = bd.plan_backfill(fs, folder="Half Book")
    assert [i.dest_path for i in items] == ["sawimages/Half Book/page_2_display.jpg"]


def test_plan_is_idempotent_after_a_run(fs):
    items = bd.plan_backfill(fs)
    bd.run_backfill(fs, items, execute=True, log=lambda m: None)
    assert bd.plan_backfill(fs) == []


# ---------------------------------------------------------------------------
# Dry-run vs execute.
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(fs):
    items = bd.plan_backfill(fs)
    lines = []
    written, errors = bd.run_backfill(fs, items, execute=False, log=lines.append)
    assert (written, errors) == (0, 0)
    assert fs.writes == []
    assert len(lines) == len(items) and all("DRY-RUN" in line for line in lines)


def test_execute_writes_real_downscaled_jpegs(fs):
    items = bd.plan_backfill(fs)
    written, errors = bd.run_backfill(fs, items, execute=True, log=lambda m: None)
    assert (written, errors) == (3, 0)
    data = fs.store["sawimages/Old Book/page_1_display.jpg"]
    with Image.open(io.BytesIO(data)) as img:
        # Downscaled to the app's DISPLAY_MAX_EDGE bound.
        from image_processing import DISPLAY_MAX_EDGE
        assert max(img.size) <= DISPLAY_MAX_EDGE
        assert img.format == "JPEG"
    # Existing display copies untouched.
    assert fs.store["sawimages/New Book/page_1_display.jpg"] == b"existing-display"


def test_execute_never_overwrites_or_deletes(fs):
    before = set(fs.store)
    items = bd.plan_backfill(fs)
    bd.run_backfill(fs, items, execute=True, log=lambda m: None)
    # Only additions, and only the planned dest paths.
    assert set(fs.writes) == {i.dest_path for i in items}
    assert before <= set(fs.store)


def test_one_bad_page_does_not_abort_the_run(fs):
    items = bd.plan_backfill(fs)  # 3-item plan
    # Break two sources AFTER planning: one vanishes (FileNotFoundError on
    # read), one is corrupt (make_display_copy returns it unchanged and the
    # decode check must refuse to upload it).
    del fs.store["sawimages/Half Book/page_2.jpg"]
    fs.store["sawimages/Old Book/page_1.jpg"] = b"not-a-jpeg"
    lines = []
    written, errors = bd.run_backfill(fs, items, execute=True, log=lines.append)
    assert errors == 2
    assert written == 1  # the remaining good page still got written
    assert "sawimages/Old Book/page_2_display.jpg" in fs.store
    # Neither bad page gained a (broken) display object.
    assert "sawimages/Half Book/page_2_display.jpg" not in fs.store
    assert "sawimages/Old Book/page_1_display.jpg" not in fs.store
    assert sum(line.startswith("ERROR") for line in lines) == 2
