"""Persist correction evidence independently of a later OCR outcome."""


def apply_page_correction(page, *, corrected, rotation_uncertain):
    """Populate an unregistered Page before OCR or from a durable result.

    Correcting the image and reading its text are separate outcomes: an OCR
    failure must never erase an already-known correction or orientation warning.
    """
    page.corrected = bool(corrected)
    page.rotation_uncertain = bool(rotation_uncertain)
