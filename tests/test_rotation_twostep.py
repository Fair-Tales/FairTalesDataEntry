"""Tests for #217 — the two-step rotation detection scheme.

Covers the DETERMINISTIC parts around the AI calls (the calls themselves are
faked): the strict one-word reply parsing, the triage->angle mapping, the
sideways +90°-in-code disambiguation (including that step 2 really sees the
rotated image), the landscape aspect gate, and the ``rotation_uncertain`` flag
replacing the old silent default-to-0 on error/uncertainty. No network, no
Streamlit.
"""

import io

import anthropic
import pytest
from PIL import Image

import image_processing as ip
import utilities

MODEL = "test-rotation-model"


def _jpeg(width, height, color=(120, 30, 30)):
    """Tiny real JPEG so ``_image_aspect``/``rotate_image`` exercise real PIL."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


PORTRAIT = _jpeg(100, 200)   # single page / sideways spread shape
LANDSCAPE = _jpeg(200, 100)  # double-page spread shape
SQUARISH = _jpeg(100, 100)


class _FakeAnthropicError(anthropic.AnthropicError):
    pass


@pytest.fixture
def vision(monkeypatch):
    """Fake ``utilities.vision_text`` (imported lazily by get_rotation_angle).

    ``record['replies']`` is consumed in order; each call's image bytes and
    prompt are recorded so tests can assert what the model was actually shown.
    """
    record = {"replies": [], "images": [], "prompts": []}

    def fake_vision_text(client, images, prompt, *, model, max_tokens):
        assert model == MODEL
        record["images"].append(images[0])
        record["prompts"].append(prompt)
        reply = record["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(utilities, "vision_text", fake_vision_text)
    return record


# ---------------------------------------------------------------------------
# Strict reply parsing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("UPRIGHT", "UPRIGHT"),
    ("upright", "UPRIGHT"),
    (" Sideways.\n", "SIDEWAYS"),
    ("UPSIDEDOWN", "UPSIDEDOWN"),
    ("UPSIDE DOWN", "UPSIDEDOWN"),      # split word normalises
    ('"UPSIDE-DOWN"', "UPSIDEDOWN"),    # punctuation stripped
])
def test_parse_accepts_single_expected_word(raw, expected):
    assert ip.parse_orientation_word(raw, ip._TRIAGE_WORDS) == expected


@pytest.mark.parametrize("raw", [
    None, "", "0", "90", "maybe UPRIGHT", "UPRIGHT or UPSIDEDOWN",
    "the image is fine", "SIDEWAYS-ISH",
])
def test_parse_rejects_anything_else(raw):
    assert ip.parse_orientation_word(raw, ip._TRIAGE_WORDS) is None


def test_parse_respects_allowed_vocabulary():
    # SIDEWAYS is not a valid answer to the step-2 binary question.
    assert ip.parse_orientation_word("SIDEWAYS", ip._BINARY_WORDS) is None


# ---------------------------------------------------------------------------
# Triage mapping (step 1 alone).
# ---------------------------------------------------------------------------

def test_upright_maps_to_zero(vision):
    vision["replies"] = ["UPRIGHT"]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (0, False)
    assert len(vision["images"]) == 1  # no second call


def test_upsidedown_maps_to_180(vision):
    vision["replies"] = ["UPSIDEDOWN"]
    assert ip.get_rotation_angle(LANDSCAPE, object(), model=MODEL) == (180, False)
    assert len(vision["images"]) == 1


# ---------------------------------------------------------------------------
# Sideways: deterministic +90° in code, then the binary question.
# ---------------------------------------------------------------------------

def test_sideways_then_upright_maps_to_90(vision):
    vision["replies"] = ["SIDEWAYS", "UPRIGHT"]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (90, False)
    assert vision["prompts"][1] is not vision["prompts"][0]
    # Step 2 must see the image ROTATED 90° clockwise in code: the 100x200
    # portrait becomes 200x100.
    with Image.open(io.BytesIO(vision["images"][1])) as img:
        assert img.size == (200, 100)


def test_sideways_then_upsidedown_maps_to_270(vision):
    vision["replies"] = ["SIDEWAYS", "UPSIDEDOWN"]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (270, False)


def test_sideways_then_garbage_is_uncertain(vision):
    vision["replies"] = ["SIDEWAYS", "I think it reads normally"]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (0, True)


def test_sideways_then_api_error_is_uncertain(vision):
    vision["replies"] = ["SIDEWAYS", _FakeAnthropicError("boom")]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (0, True)


def test_squarish_image_still_gets_step_two(vision):
    # Below the landscape threshold the gate must not fire.
    vision["replies"] = ["SIDEWAYS", "UPRIGHT"]
    assert ip.get_rotation_angle(SQUARISH, object(), model=MODEL) == (90, False)


# ---------------------------------------------------------------------------
# Aspect gate: a landscape image is expected to be a spread (0/180 only).
# ---------------------------------------------------------------------------

def test_sideways_on_landscape_is_gated_to_uncertain(vision):
    vision["replies"] = ["SIDEWAYS"]
    assert ip.get_rotation_angle(LANDSCAPE, object(), model=MODEL) == (0, True)
    # The gate resolves the disagreement WITHOUT a second model call.
    assert len(vision["images"]) == 1


def test_aspect_helper_contract():
    assert ip._image_aspect(LANDSCAPE) == pytest.approx(2.0)
    assert ip._image_aspect(PORTRAIT) == pytest.approx(0.5)
    # Undecodable bytes -> None -> the gate simply does not apply.
    assert ip._image_aspect(b"not-an-image") is None


# ---------------------------------------------------------------------------
# Uncertainty replaces the silent default-to-0.
# ---------------------------------------------------------------------------

def test_triage_api_error_is_uncertain(vision):
    vision["replies"] = [_FakeAnthropicError("transient")]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (0, True)


def test_triage_garbage_is_uncertain(vision):
    vision["replies"] = ["It appears to be a book page"]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (0, True)


def test_triage_empty_reply_is_uncertain(vision):
    vision["replies"] = [None]
    assert ip.get_rotation_angle(PORTRAIT, object(), model=MODEL) == (0, True)


# ---------------------------------------------------------------------------
# correct_page_image propagates the flag.
# ---------------------------------------------------------------------------

@pytest.fixture
def settings():
    return dict(utilities.AI_SETTINGS_DEFAULTS)


def test_pipeline_propagates_uncertain_flag(settings, monkeypatch):
    monkeypatch.setattr(
        ip, "correct_book_page", lambda raw: (b"crop", True, True)
    )
    monkeypatch.setattr(
        ip, "get_rotation_angle", lambda image, client, model=None: (0, True)
    )
    _, corrected, method, uncertain = ip.correct_page_image(b"raw", object(), settings)
    assert uncertain is True
    assert corrected == b"crop" and method == "opencv"


def test_pipeline_stage2_uncertain_no_rotation(settings, monkeypatch):
    # OpenCV fails, rotation check can't decide: no corrected artifact, but the
    # uncertainty is surfaced rather than silently treated as "no rotation".
    monkeypatch.setattr(ip, "correct_book_page", lambda raw: (None, False, False))
    monkeypatch.setattr(
        ip, "get_rotation_angle", lambda image, client, model=None: (0, True)
    )
    for_extraction, corrected, method, uncertain = ip.correct_page_image(
        b"raw", object(), settings
    )
    assert (for_extraction, corrected, method, uncertain) == (b"raw", None, None, True)


def test_pipeline_confident_zero_is_not_uncertain(settings, monkeypatch):
    monkeypatch.setattr(ip, "correct_book_page", lambda raw: (b"crop", True, True))
    monkeypatch.setattr(
        ip, "get_rotation_angle", lambda image, client, model=None: (0, False)
    )
    _, corrected, _, uncertain = ip.correct_page_image(b"raw", object(), settings)
    assert uncertain is False and corrected == b"crop"


def test_pipeline_rotation_disabled_is_not_uncertain(settings, monkeypatch):
    settings["enable_rotation_correction"] = False
    monkeypatch.setattr(ip, "correct_book_page", lambda raw: (None, False, False))
    result = ip.correct_page_image(b"raw", object(), settings)
    assert result == (b"raw", None, None, False)


def test_triage_prompt_keys_on_line_direction():
    """Regression lock: the triage prompt must decide by the DIRECTION the text
    lines run (vertical = SIDEWAYS), NOT by an abstract "quarter turn vs half
    turn" amount. On portrait-shot double-page spreads the turn-amount wording
    made the model confuse SIDEWAYS (90°) with UPSIDEDOWN (180°) non-
    deterministically, leaving spreads sideways; keying on horizontal-vs-vertical
    line direction fixed it (38/38 vs 29/38 on a production sample). Do not revert
    to the turn-amount framing without re-measuring.
    """
    from text_content import AIPrompts

    prompt = AIPrompts.rotation_triage.lower()
    # Decision is anchored on line direction...
    assert "horizontal" in prompt and "vertical" in prompt
    assert "lines of text" in prompt or "lines of printed text" in prompt
    # ...and still names all three verdicts for the strict parser.
    for word in ("upright", "upsidedown", "sideways"):
        assert word in prompt
