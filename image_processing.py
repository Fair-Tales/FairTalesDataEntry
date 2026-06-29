"""
Two-stage book page image correction pipeline.

Stage 1 (correct_book_page): OpenCV detects the book page boundary and applies
a perspective transform to produce a straight, cropped image.

Stage 2 (check_crop_quality): Claude Haiku 4.5 verifies the result looks like
a proper book page before it is committed to S3.
"""

import base64
import io
import re

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

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


def downscale_for_vision(image_bytes, max_edge=1568, quality=85):
    """Downscale and normalise an image for a Claude vision request.

    Phone photos are commonly several megabytes and many megapixels. Sent raw,
    a single one can approach Claude's per-image (~5MB) size limit, and a
    multi-image request (e.g. ``locate_key_pages`` sending every page of a book
    at once) easily exceeds the ~32MB per-request limit, raising an
    ``anthropic.AnthropicError`` before any metadata can be extracted (#84).

    This bakes in any EXIF orientation (matching ``exif_transpose_bytes``),
    converts to RGB, and resizes so the longest edge is at most ``max_edge``
    (Claude's vision sweet spot — larger images are downsampled server-side
    anyway, so sending them only wastes payload). The result is re-encoded as
    JPEG, which also normalises PNG/HEIC inputs to the declared
    ``image/jpeg`` media type used by the vision callers.

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
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        return buf.getvalue()
    except (UnidentifiedImageError, OSError):
        # Undecodable or truncated image: return the original bytes so the
        # caller can still handle/send it rather than crashing here.
        return image_bytes


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


def get_rotation_angle(image_bytes, client):
    """
    Ask Claude Sonnet to estimate the clockwise rotation angle needed to
    make the book page text read horizontally.
    Returns an integer angle in degrees, or 0 if no rotation is needed,
    the model is uncertain, or the API call fails.
    """
    image_data = base64.standard_b64encode(downscale_for_vision(image_bytes)).decode('utf-8')
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": AIPrompts.rotation_angle},
                ],
            }]
        )
        raw = response.content[0].text.strip()
        match = re.search(r'-?\d+', raw)
        if not match:
            return 0
        angle = int(match.group())
        return 0 if abs(angle) < 5 else angle
    except Exception:
        return 0


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


def check_crop_quality(image_bytes, client):
    """
    Ask Claude Haiku 4.5 whether the corrected image looks like a properly
    cropped, right-way-up book page. Returns True only on a clear 'yes'.
    Defaults to True on API errors so a successful OpenCV result is not
    discarded due to a transient network failure.
    """
    image_data = base64.standard_b64encode(downscale_for_vision(image_bytes)).decode('utf-8')
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": AIPrompts.crop_quality_check},
                ],
            }],
        )
        return response.content[0].text.strip().lower().startswith("yes")
    except Exception:
        return True  # network/API error: trust OpenCV's geometric result
