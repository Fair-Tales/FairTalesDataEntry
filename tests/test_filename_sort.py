"""Tests for filename ordering of uploaded page photos (issue #108).

The upload pages (``pages/uploader.py``, ``pages/add_books_batch.py``,
``pages/add_book_photos.py``) reconstruct page reading order purely from the
*filename* of each uploaded photo, using ``natsort.natsorted`` on the set of
filenames the user multi-selected:

    file_dict = {file.name: file for file in uploaded_files}
    sorted_names = natsort.natsorted(list(file_dict.keys()), reverse=False)

These tests do NOT change that sorting logic. They lock in the behaviour for
representative real-device filename sets so we can write accurate upload
instructions (telling archivists they may upload in any order and the app will
reconstruct capture order from the filename) and so a future regression in the
sort would be caught.

Each test mirrors the production call exactly: ``natsort.natsorted`` on a list of
plain filename strings, default settings, ``reverse=False``.
"""

import random

import natsort
import pytest


def sort_like_app(names):
    """Sort filenames exactly as the upload pages do.

    Mirrors ``natsort.natsorted(list(file_dict.keys()), reverse=False)``.
    The input is deliberately shuffled by callers because Streamlit's
    ``file_uploader`` does not guarantee selection order, so the sort must be
    order-independent.
    """
    return natsort.natsorted(names, reverse=False)


# ---------------------------------------------------------------------------
# iPhone: IMG_0001.JPG ... — zero-padded to 4 digits, rolls to 5 at IMG_9999.
# ---------------------------------------------------------------------------

def test_iphone_sequential_9_to_10_boundary():
    """iPhone names are zero-padded, so even plain string sort works; natsort
    must keep IMG_0009 before IMG_0010 (the classic 9->10 boundary)."""
    expected = [f"IMG_{n:04d}.JPG" for n in range(1, 16)]
    shuffled = expected[:]
    random.Random(0).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_iphone_9999_to_10000_rollover():
    """When the iPhone counter rolls from 4 to 5 digits (IMG_9999 -> IMG_10000)
    the zero-padding stops growing. Plain string sort would put '10000' before
    '9999'; natsort orders by numeric value and gets it right."""
    expected = [
        "IMG_9998.JPG",
        "IMG_9999.JPG",
        "IMG_10000.JPG",
        "IMG_10001.JPG",
    ]
    shuffled = expected[:]
    random.Random(1).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_iphone_mixed_case_extension():
    """Real iPhone exports may mix .JPG/.HEIC/.jpg case across a set. Within a
    single capture session the extension is consistent; natsort still orders by
    the numeric counter regardless of extension case here."""
    expected = [f"IMG_{n:04d}.HEIC" for n in range(1, 6)]
    shuffled = expected[:]
    random.Random(2).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


# ---------------------------------------------------------------------------
# Android - Google Pixel: PXL_YYYYMMDD_HHMMSSmmm.jpg (millisecond timestamp).
# ---------------------------------------------------------------------------

def test_pixel_pxl_timestamp_sequence():
    """Pixel camera names embed a millisecond timestamp; taken sequentially the
    full string is monotonically increasing, so natsort orders capture order."""
    expected = [
        "PXL_20240101_120000123.jpg",
        "PXL_20240101_120005456.jpg",
        "PXL_20240101_120011789.jpg",
        "PXL_20240101_120030001.jpg",
        "PXL_20240101_120131999.jpg",
    ]
    shuffled = expected[:]
    random.Random(3).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_pixel_minute_and_hour_rollover():
    """Timestamp rolls over seconds/minutes/hours; still monotonic as a string,
    and natsort agrees."""
    expected = [
        "PXL_20240101_115959123.jpg",  # 11:59:59
        "PXL_20240101_120000456.jpg",  # 12:00:00 (minute+second rollover)
        "PXL_20240101_125959789.jpg",  # 12:59:59
        "PXL_20240101_130000001.jpg",  # 13:00:00 (hour rollover)
    ]
    shuffled = expected[:]
    random.Random(4).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


# ---------------------------------------------------------------------------
# Generic timestamp names: YYYYMMDD_HHMMSS.jpg (many Android camera apps).
# ---------------------------------------------------------------------------

def test_timestamp_midnight_rollover():
    """Across midnight the *date* portion increments, so 235959 -> 000001 of the
    next day still sorts correctly because the leading date dominates."""
    expected = [
        "20240101_235958.jpg",
        "20240101_235959.jpg",
        "20240102_000000.jpg",
        "20240102_000001.jpg",
    ]
    shuffled = expected[:]
    random.Random(5).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_timestamp_with_img_prefix():
    """Some Android apps use IMG_YYYYMMDD_HHMMSS.jpg. Same monotonic property."""
    expected = [
        "IMG_20240101_235959.jpg",
        "IMG_20240102_000000.jpg",
        "IMG_20240102_000100.jpg",
    ]
    shuffled = expected[:]
    random.Random(6).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


# ---------------------------------------------------------------------------
# Mixed / zero-padded edge sets.
# ---------------------------------------------------------------------------

def test_zero_padded_three_digit_set():
    """A consistently zero-padded set (e.g. page001.jpg) sorts correctly."""
    expected = [f"page{n:03d}.jpg" for n in (1, 2, 9, 10, 11, 99, 100, 101)]
    shuffled = expected[:]
    random.Random(7).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_front_cover_then_spreads_same_prefix():
    """Realistic capture: a portrait cover then landscape spreads, all from the
    same device so they share the IMG_#### scheme and stay in capture order."""
    expected = [f"IMG_{n:04d}.JPG" for n in range(100, 113)]
    shuffled = expected[:]
    random.Random(8).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


# ---------------------------------------------------------------------------
# Documented mis-order cases: where native naming / mixing would NOT reconstruct
# capture order. These assert the ACTUAL natsort behaviour (so the tests pass)
# and the comments explain why archivists must avoid these situations.
# ---------------------------------------------------------------------------

def test_unpadded_numbers_DO_sort_numerically_under_natsort():
    """Unpadded numbers (photo1.jpg, photo2.jpg, ... photo10.jpg) are exactly the
    case plain alphabetical sort gets WRONG ('10' < '2' as strings). natsort's
    whole purpose is to fix this, and it does: numeric value wins. So an unpadded
    device scheme is still safe *as long as the whole set shares one prefix*."""
    expected = [f"photo{n}.jpg" for n in range(1, 13)]
    shuffled = expected[:]
    random.Random(9).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_mixed_prefixes_interleave_by_prefix_NOT_capture_order():
    """MIS-ORDER CASE. If a set mixes two naming schemes (e.g. photos taken on an
    iPhone 'IMG_*' and copied-in 'PXL_*' from another phone), natsort groups by
    the differing text prefix first ('IMG' < 'PXL'), so ALL IMG_* come before ALL
    PXL_* regardless of when each was actually captured.

    This is the key real-world risk: do NOT combine photos of one book from two
    different devices/apps in a single upload, because filename order will follow
    the prefix, not capture order."""
    img = [f"IMG_{n:04d}.JPG" for n in range(1, 4)]      # captured 1st, 3rd, 5th
    pxl = [f"PXL_20240101_12000{n}000.jpg" for n in range(0, 3)]  # 2nd, 4th, 6th
    true_capture_order = [img[0], pxl[0], img[1], pxl[1], img[2], pxl[2]]
    shuffled = (img + pxl)[:]
    random.Random(10).shuffle(shuffled)
    result = sort_like_app(shuffled)
    # natsort does NOT recover the interleaved capture order:
    assert result != true_capture_order
    # instead it groups by prefix then number:
    assert result == img + pxl


def test_inconsistent_padding_same_prefix_still_ok():
    """Even if padding width is inconsistent within one prefix (IMG_9.jpg vs
    IMG_010.jpg), natsort compares the numeric value, so it still orders
    correctly. Documents that padding *consistency* is not required, only a
    consistent text prefix."""
    expected = ["IMG_9.jpg", "IMG_010.jpg", "IMG_0011.jpg", "IMG_12.jpg"]
    shuffled = expected[:]
    random.Random(11).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


def test_whatsapp_style_names_sort_by_sequence_suffix():
    """MIS-ORDER RISK. WhatsApp/share-sheet renames like
    'IMG-20240101-WA0001.jpg' embed a date then a per-day sequence. Within one
    day they sort fine, but note the date and WA sequence are independent: a
    photo shared the next day (WA0001 again) would sort before a later same-day
    one only if the date leads. Here the date leads, so order holds."""
    expected = [
        "IMG-20240101-WA0009.jpg",
        "IMG-20240101-WA0010.jpg",
        "IMG-20240102-WA0001.jpg",
    ]
    shuffled = expected[:]
    random.Random(12).shuffle(shuffled)
    assert sort_like_app(shuffled) == expected


@pytest.mark.parametrize(
    "names,expected",
    [
        # idempotent: already-sorted stays sorted
        (["IMG_0001.JPG", "IMG_0002.JPG"], ["IMG_0001.JPG", "IMG_0002.JPG"]),
        # single file
        (["IMG_0001.JPG"], ["IMG_0001.JPG"]),
        # empty set (no files) returns empty
        ([], []),
    ],
)
def test_edge_inputs(names, expected):
    assert sort_like_app(names) == expected
