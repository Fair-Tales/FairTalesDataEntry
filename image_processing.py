"""
Two-stage book page image correction pipeline.

Stage 1 (correct_book_page): OpenCV detects the book page boundary and applies
a perspective transform to produce a straight, cropped image.

Stage 2 (check_crop_quality): Claude Haiku 4.5 verifies the result looks like
a proper book page before it is committed to S3.
"""

import base64
import io

import cv2
import numpy as np
from PIL import Image

from text_content import AIPrompts


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

    Returns (corrected_bytes, success):
    - corrected_bytes: JPEG bytes of the corrected image, or None on failure
    - success: True if a plausible book page boundary was found and corrected
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None, False

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
        return None, False

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
        return buf.getvalue(), True

    return None, False


def check_crop_quality(image_bytes, client):
    """
    Ask Claude Haiku 4.5 whether the corrected image looks like a properly
    cropped, right-way-up book page. Returns True only on a clear 'yes'.
    Defaults to True on API errors so a successful OpenCV result is not
    discarded due to a transient network failure.
    """
    image_data = base64.standard_b64encode(image_bytes).decode('utf-8')
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
