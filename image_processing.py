"""
Two-stage book page image correction pipeline.

Stage 1 (correct_book_page): OpenCV detects the book page boundary and applies
a perspective transform to produce a straight, cropped image.

Stage 2 (check_crop_quality): Claude Haiku 4.5 verifies the result looks like
a proper book page before it is committed to S3.
"""

import io
import logging
import re

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Register HEIC/HEIF decoding so PIL can read iPhone (and Android HEIF) photos.
# iPhones default to HEIC, which Claude's vision API cannot read directly; decoding
# it here means downscale_for_vision / exif_transpose_bytes re-encode it to JPEG
# transparently. If the optional dependency is unavailable, HEIC simply isn't
# supported (the app still runs — no crash).
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from text_content import AIPrompts


def exif_transpose_bytes(image_bytes):
    """
    Bake any EXIF orientation tag into the pixel data and return JPEG bytes.

    Phone cameras commonly store portrait photos as landscape pixels plus an
    EXIF Orientation tag describing how the viewer should rotate them. Neither
    OpenCV (``cv2.imdecode``) nor a plain PIL/Streamlit display applies that
    tag, so such photos appear rotated/sideways. Applying the transpose once,
    here, at upload time means every downstream stage (perspective correction,
    AI text extraction, on-screen display) operates on a correctly-oriented
    image.

    Returns the original bytes unchanged when there is no orientation to apply
    (tag absent or equal to 1) or when the bytes cannot be decoded as an image.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        orientation = img.getexif().get(0x0112)  # 0x0112 == Orientation tag
        if orientation in (None, 1):
            return image_bytes
        transposed = ImageOps.exif_transpose(img)
        if transposed.mode not in ('RGB', 'L'):
            transposed = transposed.convert('RGB')
        buf = io.BytesIO()
        transposed.save(buf, format='JPEG', quality=95)
        return buf.getvalue()
    except (UnidentifiedImageError, OSError):
        # Undecodable or truncated image: return the original bytes untouched
        # so the caller can still store/handle the upload as-is.
        return image_bytes


#: Claude's hard per-image byte cap for vision requests (#134). The raw multi-MB
#: phone photos that breached this are always JPEG re-encoded below it by
#: ``downscale_for_vision``; the cap is enforced explicitly so the higher-res
#: extraction path (``max_edge=2576``, #135) cannot reintroduce the oversized
#: rejection on a rare very dense page. A safety margin under the 10MB API limit.
_VISION_MAX_BYTES = 9 * 1024 * 1024


def downscale_for_vision(image_bytes, max_edge=1568, quality=85,
                         max_bytes=_VISION_MAX_BYTES):
    """Downscale and normalise an image for a Claude vision request.

    Phone photos are commonly several megabytes and many megapixels. Sent raw,
    a single one can approach Claude's per-image (~5MB) size limit, and a
    multi-image request (e.g. ``locate_key_pages`` sending every page of a book
    at once) easily exceeds the ~32MB per-request limit, raising an
    ``anthropic.AnthropicError`` before any metadata can be extracted (#84).

    This bakes in any EXIF orientation (matching ``exif_transpose_bytes``),
    converts to RGB, and resizes so the longest edge is at most ``max_edge``
    (Claude's vision sweet spot at the ~1568px default — larger images are
    downsampled server-side anyway, so sending them only wastes payload). The
    result is re-encoded as JPEG, which also normalises PNG/HEIC inputs to the
    declared ``image/jpeg`` media type used by the vision callers.

    The DATA-EXTRACTION callers pass a higher ``max_edge`` (2576px, #135) for
    better OCR of dense text. ``max_bytes`` keeps the encoded result under
    Claude's 10MB per-image cap regardless of ``max_edge`` (#134): if a rare very
    dense page still exceeds it at the requested ``quality``, JPEG quality is
    stepped down until it fits (or the floor is reached and the smallest attempt
    is sent). Pass ``max_bytes=None`` to disable the cap.

    Only shrinks: images already within ``max_edge`` are re-encoded without
    upscaling. On an unreadable/truncated image the original bytes are returned
    unchanged so the caller can still attempt the request rather than crash.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        longest_edge = max(img.size)
        if longest_edge > max_edge:
            scale = max_edge / longest_edge
            new_size = (
                max(1, round(img.width * scale)),
                max(1, round(img.height * scale)),
            )
            img = img.resize(new_size, Image.LANCZOS)

        def _encode(q):
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=q)
            return buf.getvalue()

        data = _encode(quality)
        # Enforce Claude's per-image byte cap (#134): step quality down until the
        # encoded JPEG fits, so the high-res extraction path stays under the limit.
        current_quality = quality
        while max_bytes is not None and len(data) > max_bytes and current_quality > 40:
            current_quality -= 15
            data = _encode(current_quality)
        return data
    except (UnidentifiedImageError, OSError):
        # Undecodable or truncated image: return the original bytes so the
        # caller can still handle/send it rather than crashing here.
        return image_bytes


#: Long-edge (px) for the lightweight on-screen page derivative (#184). The full
#: resolution page photos are multi-MB quality-95 JPEGs; enter-text only needs a
#: screen-sized copy, so a ~1200px derivative cuts the S3 fetch and browser
#: transfer ~10x while staying crisp on a laptop/phone. Enlarge / crop keep using
#: the full-res original.
DISPLAY_MAX_EDGE = 1200


def make_display_copy(image_bytes, max_edge=DISPLAY_MAX_EDGE, quality=80):
    """Return JPEG bytes of a screen-sized display derivative of a page image (#184).

    Reuses ``downscale_for_vision``'s resize+encode (only shrinks, bakes in EXIF
    orientation, normalises to JPEG). ``max_bytes=None`` because the display copy
    is bandwidth-bounded by ``max_edge`` alone — there is no per-image API cap to
    honour here. Written alongside the raw/corrected page image at processing time
    so enter-text can ship the small copy instead of the multi-MB original.
    """
    return downscale_for_vision(
        image_bytes, max_edge=max_edge, quality=quality, max_bytes=None
    )


def is_black_frame(image_bytes, mean_threshold=10.0, percentile=99,
                   percentile_threshold=40.0):
    """
    Detect a fully black "separator" frame in a sequential photo batch (#84).

    The batch multi-book upload protocol (issue #84) asks the user to cover the
    camera lens and take a fully black photo between consecutive books. Such a
    frame carries almost no light: its average pixel brightness sits near 0 and
    even its brightest pixels stay dark. This cheap PIL/NumPy check flags those
    frames so the batch can be split into per-book groups; the separator frames
    themselves are then discarded (never stored as pages).

    Brightness is measured on the 0-255 grayscale scale, and BOTH conditions
    must hold for a frame to count as a separator:
      - mean brightness < ``mean_threshold`` (default 10 — i.e. under ~4% of
        full brightness on average); AND
      - the ``percentile``-th brightest pixel < ``percentile_threshold``
        (default: the 99th percentile must be below 40).

    The percentile guard is what stops a genuinely dark-but-real page (a black
    book cover with light title text, a lens glint, a dim photo) from being
    mistaken for a separator: those have a small number of bright pixels that
    push the high percentile up even when the mean is low. Defaults are tuned
    for lens-covered photos, which are near-uniformly black.

    Returns False for undecodable/empty bytes so a corrupt image is treated as
    an ordinary page rather than a (spurious) book boundary.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('L')
    except (UnidentifiedImageError, OSError):
        return False
    arr = np.asarray(img, dtype=np.float32)
    if arr.size == 0:
        return False
    mean_brightness = float(arr.mean())
    bright_pixel = float(np.percentile(arr, percentile))
    return mean_brightness < mean_threshold and bright_pixel < percentile_threshold


def _order_points(pts):
    """Order four corner points as: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]   # top-left: smallest x+y
    rect[2] = pts[np.argmax(s)]   # bottom-right: largest x+y
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right: smallest y-x
    rect[3] = pts[np.argmax(diff)]  # bottom-left: largest y-x
    return rect


def correct_book_page(image_bytes):
    """
    Attempt to detect the book page boundary and correct perspective/rotation.

    Returns (corrected_bytes, success, high_confidence):
    - corrected_bytes: JPEG bytes of the corrected image, or None on failure
    - success: True if a plausible book page boundary was found and corrected
    - high_confidence: True only when the accepted boundary is a *well-framed,
      clearly portrait-proportioned single page* — a large crop (45-88% of the
      frame) whose output aspect ratio (width/height) is 0.55-0.85. Such a
      detection is almost certainly the real, upright page, so the caller may
      skip the extra Haiku crop-quality check to save a per-page model call
      (#110). The band is deliberately narrow and conservative: a page
      photographed sideways warps to a *landscape* aspect (>1) and a double-page
      spread is landscape too, so neither qualifies — both still get verified.
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None, False, False

    h, w = img.shape[:2]

    # Enhance contrast before edge detection to help with low-contrast covers
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    # Dilate to close small gaps in book edges
    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, False, False

    # Try the five largest contours in case the first isn't the book
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

        if len(approx) != 4:
            continue

        area_ratio = cv2.contourArea(approx) / (h * w)
        if not (0.20 <= area_ratio <= 0.95):
            continue

        pts = _order_points(approx.reshape(4, 2).astype(np.float32))

        out_w = int(max(
            np.linalg.norm(pts[2] - pts[3]),
            np.linalg.norm(pts[1] - pts[0]),
        ))
        out_h = int(max(
            np.linalg.norm(pts[1] - pts[2]),
            np.linalg.norm(pts[0] - pts[3]),
        ))

        if out_w < 400 or out_h < 300:
            continue

        if not (0.3 <= out_w / out_h <= 3.5):
            continue

        dst = np.array(
            [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
            dtype=np.float32,
        )
        M = cv2.getPerspectiveTransform(pts, dst)
        warped = cv2.warpPerspective(img, M, (out_w, out_h))

        pil_img = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        buf = io.BytesIO()
        pil_img.save(buf, format='JPEG', quality=95)

        aspect = out_w / out_h
        high_confidence = (0.45 <= area_ratio <= 0.88) and (0.55 <= aspect <= 0.85)
        return buf.getvalue(), True, high_confidence

    return None, False, False


def get_rotation_angle(image_bytes, client, model=None):
    """
    Ask Claude (a cheap routing/QC vision call) for the clockwise rotation
    needed to make the book page text read the RIGHT WAY UP — including the
    full 180° upside-down case, not just 90° sideways (#154).

    The prompt (``AIPrompts.rotation_angle``) asks for exactly one of
    0/90/180/270. This parses the first integer out of the reply, normalises it
    modulo 360 (so a stray ``-90`` becomes ``270`` and ``360`` becomes ``0``),
    and snaps it to the nearest quarter turn. Snapping keeps a slightly-off
    estimate (e.g. ``178``) from failing to correct an upside-down page, and
    matches the granularity of a page-orientation fix — the fine-perspective
    deskew is handled geometrically upstream in ``correct_book_page``.

    Returns 0/90/180/270. Returns 0 when no rotation is needed, the model is
    uncertain / gives no number, or the API call fails.

    ``model`` defaults to the admin-configured ``rotation_model`` (falling back to
    ``claude-sonnet-4-6``) so the routing model can be re-tuned globally without a
    deploy; callers may still pass an explicit model.
    """
    import anthropic
    from utilities import vision_text, get_ai_settings

    if model is None:
        model = get_ai_settings()['rotation_model']

    try:
        raw = vision_text(
            client, [image_bytes], AIPrompts.rotation_angle,
            model=model, max_tokens=10,
        )
    except anthropic.AnthropicError as exc:
        # Narrowed from a broad ``except`` (#127): a transient API failure means
        # "assume no rotation", but is logged rather than silently swallowed.
        logger.warning("get_rotation_angle: vision call failed: %s", exc)
        return 0
    if not raw:
        return 0
    match = re.search(r'-?\d+', raw)
    if not match:
        return 0
    # Normalise to [0, 360) then snap to the nearest quarter turn. Candidate 360
    # collapses back to 0 via the modulo, so angles just under a full turn
    # (e.g. 350) round to 0 rather than an invalid 360.
    angle = int(match.group()) % 360
    return min((0, 90, 180, 270, 360), key=lambda q: abs(q - angle)) % 360


def rotate_image(image_bytes, angle_degrees):
    """
    Rotate image bytes clockwise by angle_degrees. expand=True ensures no
    content is cropped by the rotation. Returns JPEG bytes.
    """
    img = Image.open(io.BytesIO(image_bytes))
    rotated = img.rotate(-angle_degrees, expand=True)  # PIL rotates counter-clockwise
    buf = io.BytesIO()
    rotated.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


def apply_manual_correction(
    img,
    rotation=0,
    fine_angle=0,
    crop_left=0,
    crop_right=0,
    crop_top=0,
    crop_bottom=0,
):
    """Apply the manual crop-and-rotate editor's transform to a PIL image.

    This is the SINGLE implementation of the enter-text "Crop and rotate"
    dialog's preview/save transform (#209) — extracted from
    ``pages/enter_text.manual_correction_dialog`` so the option→transform
    mapping is pure and unit-testable (no Streamlit).

    Args:
        img: source PIL image. Never mutated — PIL ``rotate``/``crop`` return
            new images (callers may pass a cached image safely).
        rotation: accumulated quarter-turn rotation in degrees, CLOCKWISE
            positive (the dialog's "90° right" adds +90, "90° left" adds -90,
            "180°" adds +180).
        fine_angle: fine-adjustment angle in degrees, clockwise positive.
        crop_left/right/top/bottom: percentage (0-100) to trim from each edge.
            Ignored when an axis' pair sums to >= 100 or would invert.

    Returns the transformed PIL image (the input object itself when every
    parameter is zero/no-op).
    """
    total_angle = rotation + fine_angle
    if total_angle != 0:
        # PIL rotates counter-clockwise for positive angles; the editor's
        # convention is clockwise positive, hence the negation. expand=True so
        # no content is lost on non-quarter angles.
        img = img.rotate(-total_angle, expand=True)

    w, h = img.size
    if crop_left + crop_right < 100 and crop_top + crop_bottom < 100:
        left = int(w * crop_left / 100)
        right = int(w * (1 - crop_right / 100))
        top_px = int(h * crop_top / 100)
        bottom_px = int(h * (1 - crop_bottom / 100))
        if right > left and bottom_px > top_px:
            img = img.crop((left, top_px, right, bottom_px))
    return img


def correct_page_image(raw_bytes, ai_client, ai_settings, report=None):
    """Run the staged crop/rotation correction pipeline for one page — pure
    compute + AI calls only, with NO S3, Firestore or Streamlit access, so it is
    callable from the background page-processing worker (#179) as well as from
    ``pages.uploader._process_page`` (which adds the S3 save).

    Stage 1: OpenCV perspective correction (+ Haiku crop-quality check, unless
    the crop is high-confidence) + orientation check on the accepted crop.
    Stage 2: rotation-only fallback on the raw photo.

    ``ai_settings`` is a plain validated settings dict (``get_ai_settings()``,
    read by the CALLER — never here, so the Streamlit cache is not touched from
    a worker thread). ``report`` is an optional callable taking one of the
    ``Uploader.substep_*`` templates (see ``uploader._make_reporter``).

    Model-call reduction (#110): a *high-confidence*, well-framed portrait crop
    from OpenCV is trusted and the Haiku crop-quality check is skipped. That
    skip previously bypassed the ORIENTATION check too, which is how upside-down
    photos of single pages — which crop to exactly the same well-framed portrait
    geometry as upright ones — sailed through and were saved rotated 180°
    (#181). The crop verification stays skipped (cropping is best-effort), but
    orientation is now ALWAYS checked: the high-confidence path runs the cheap
    rotation call on the corrected crop and applies any returned quarter-turn
    before the crop is accepted. Orientation is never skipped while rotation
    correction is enabled.

    Returns ``(bytes_for_extraction, corrected_bytes_or_None, method)`` where
    ``method`` is ``'opencv'``, ``'rotation'`` or ``None`` (no correction).
    ``corrected_bytes`` is the artifact to persist as ``page_{n}_cropped.jpg``
    when not ``None``.
    """
    from text_content import Uploader

    report = report or (lambda _template: None)

    crop_gate_on = ai_settings['enable_crop_quality_gate']
    rotation_on = ai_settings['enable_rotation_correction']
    crop_model = ai_settings['crop_quality_model']
    rotation_model = ai_settings['rotation_model']

    # Stage 1 — OpenCV perspective correction
    report(Uploader.substep_correcting)
    corrected_bytes, opencv_ok, high_confidence = correct_book_page(raw_bytes)
    if opencv_ok:
        if high_confidence:
            # Trust the geometry (skip the Haiku crop verification, #110) but
            # never the orientation (#181): verify/fix the rotation of the crop.
            if rotation_on:
                report(Uploader.substep_detecting_rotation)
                angle = get_rotation_angle(
                    corrected_bytes, ai_client, model=rotation_model
                )
                if angle != 0:
                    corrected_bytes = rotate_image(corrected_bytes, angle)
            return corrected_bytes, corrected_bytes, 'opencv'
        if crop_gate_on:
            report(Uploader.substep_checking_crop)
            crop_ok = check_crop_quality(corrected_bytes, ai_client, model=crop_model)
        else:
            # Gate disabled by the admin: trust the geometric result directly.
            crop_ok = True
        if crop_ok:
            # The crop-quality gate (when ON) also vets orientation and rejects
            # an upside-down crop, routing it to the Stage-2 rotation fallback
            # below. But with the gate OFF that safety net is gone, so an
            # upside-down page/cover that OpenCV cropped cleanly would be saved
            # uncorrected. Honour the "orientation is never skipped while
            # rotation correction is enabled" invariant by running the cheap
            # rotation check on the accepted crop here too (#181 follow-up).
            if rotation_on and not crop_gate_on:
                report(Uploader.substep_detecting_rotation)
                angle = get_rotation_angle(
                    corrected_bytes, ai_client, model=rotation_model
                )
                if angle != 0:
                    corrected_bytes = rotate_image(corrected_bytes, angle)
            return corrected_bytes, corrected_bytes, 'opencv'

    # Stage 2 — rotation-only fallback.
    #
    # Rotation fix (audit item 3): when the model detects a non-zero rotation the
    # rotated bytes are the CORRECT orientation for OCR and must be used
    # unconditionally. The rotated frame is always persisted as the ``_cropped``
    # artifact (no crop-quality gate here — since Stage 1 already rejected or
    # failed the geometric crop there is no cropped alternative left for the
    # Haiku check to approve, and a crop failure must only ever cost the CROP,
    # never the rotation). Fallback chain: rotated+cropped -> (crop rejected/
    # failed) rotated-only -> (rotation also not detected) raw.
    if rotation_on:
        report(Uploader.substep_detecting_rotation)
        angle = get_rotation_angle(raw_bytes, ai_client, model=rotation_model)
        if angle != 0:
            rotated_bytes = rotate_image(raw_bytes, angle)
            return rotated_bytes, rotated_bytes, 'rotation'

    return raw_bytes, None, None


def check_crop_quality(image_bytes, client, model=None):
    """
    Ask Claude Haiku 4.5 whether the corrected image looks like a properly
    cropped, right-way-up book page. Returns True only on a clear 'yes'.
    Defaults to True on API errors so a successful OpenCV result is not
    discarded due to a transient network failure.

    ``model`` defaults to the admin-configured ``crop_quality_model`` (falling
    back to ``claude-haiku-4-5``) so the QC model can be re-tuned globally without
    a deploy; callers may still pass an explicit model.
    """
    import anthropic
    from utilities import vision_text, get_ai_settings

    if model is None:
        model = get_ai_settings()['crop_quality_model']

    try:
        raw = vision_text(
            client, [image_bytes], AIPrompts.crop_quality_check,
            model=model, max_tokens=5,
        )
    except anthropic.AnthropicError as exc:
        # Narrowed from a broad ``except`` (#127): a network/API error means we
        # trust OpenCV's geometric result, but it is logged not swallowed.
        logger.warning("check_crop_quality: vision call failed; trusting OpenCV: %s", exc)
        return True
    if raw is None:
        return True  # no text block: trust OpenCV's geometric result (unchanged)
    return raw.lower().startswith("yes")
