"""Unit tests for #209 — the manual crop/rotate option→transform mapping.

The enter-text "Crop and rotate" dialog accumulates quarter-turn clicks into a
clockwise-positive rotation and applies it (plus fine angle and edge crops) via
``image_processing.apply_manual_correction``. These tests pin each option to the
exact expected pixel transform, and the accumulation arithmetic the dialog uses
(left = -90, right = +90, 180 = +180).
"""

import numpy as np
from PIL import Image

from image_processing import apply_manual_correction


def _test_image():
    """A 4x2 RGB image with four distinct corner colours (asymmetric)."""
    arr = np.zeros((2, 4, 3), dtype=np.uint8)
    arr[0, 0] = (255, 0, 0)      # top-left red
    arr[0, 3] = (0, 255, 0)      # top-right green
    arr[1, 0] = (0, 0, 255)      # bottom-left blue
    arr[1, 3] = (255, 255, 0)    # bottom-right yellow
    return Image.fromarray(arr)


def _px(img):
    return np.asarray(img)


def test_rotate_90_right_is_clockwise():
    img = _test_image()
    out = apply_manual_correction(img, rotation=+90)  # one "90° right" click
    assert out.size == (2, 4)  # dimensions swap
    expected = img.transpose(Image.Transpose.ROTATE_270)  # PIL: 270° CCW == 90° CW
    assert np.array_equal(_px(out), _px(expected))


def test_rotate_90_left_is_counter_clockwise():
    img = _test_image()
    out = apply_manual_correction(img, rotation=-90)  # one "90° left" click
    assert out.size == (2, 4)
    expected = img.transpose(Image.Transpose.ROTATE_90)  # 90° CCW
    assert np.array_equal(_px(out), _px(expected))


def test_rotate_180_is_a_full_half_turn():
    """The reported bug: the 180° option must produce a true half-turn."""
    img = _test_image()
    out = apply_manual_correction(img, rotation=+180)  # one "180°" click
    assert out.size == img.size  # dimensions preserved
    expected = img.transpose(Image.Transpose.ROTATE_180)
    assert np.array_equal(_px(out), _px(expected))
    # A half-turn is NOT a quarter-turn: corners must have swapped diagonally.
    assert tuple(_px(out)[0, 0]) == (255, 255, 0)   # yellow now top-left
    assert tuple(_px(out)[1, 3]) == (255, 0, 0)     # red now bottom-right


def test_rotate_270_equals_three_rights_and_one_left():
    img = _test_image()
    three_rights = apply_manual_correction(img, rotation=90 * 3)
    one_left = apply_manual_correction(img, rotation=-90)
    assert np.array_equal(_px(three_rights), _px(one_left))


def test_accumulated_clicks_match_single_rotation():
    """Two right clicks (90+90) must equal one 180 click, as the dialog
    accumulates button clicks additively into _manual_rotation."""
    img = _test_image()
    accumulated = apply_manual_correction(img, rotation=90 + 90)
    single = apply_manual_correction(img, rotation=180)
    assert np.array_equal(_px(accumulated), _px(single))


def test_zero_rotation_is_identity_and_never_mutates():
    img = _test_image()
    before = _px(img).copy()
    out = apply_manual_correction(img, rotation=0)
    assert np.array_equal(_px(out), before)
    apply_manual_correction(img, rotation=180)
    assert np.array_equal(_px(img), before)  # source untouched


def test_crop_percentages_trim_expected_pixels():
    arr = np.arange(100 * 50 * 3, dtype=np.uint8).reshape((50, 100, 3))
    img = Image.fromarray(arr)
    out = apply_manual_correction(
        img, crop_left=25, crop_right=25, crop_top=20, crop_bottom=20
    )
    assert out.size == (50, 30)  # 100-25%-25% wide, 50-20%-20% tall
    assert np.array_equal(_px(out), arr[10:40, 25:75])


def test_degenerate_crop_is_ignored():
    img = _test_image()
    out = apply_manual_correction(img, crop_left=60, crop_right=60)
    assert out.size == img.size
