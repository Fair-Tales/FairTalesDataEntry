"""Regression lock for #180 — the post-processing photos message must read as a
"here's what happened / what's next" status, never as a duplicate-upload warning.

Users hit ``Instructions.photos_already_uploaded`` (via
``pages/page_photo_upload.already_uploaded_options``) IMMEDIATELY after their own
upload finishes processing (QR-phone flow reload / return visit), so wording like
"you have already uploaded photos for this book" read as an error. Commit 3177286
reworded it; these tests keep the load-bearing properties from regressing.
"""

from text_content import Instructions


def test_message_is_not_a_duplicate_upload_warning():
    text = Instructions.photos_already_uploaded.lower()
    assert "you have already uploaded" not in text
    assert "already uploaded photos for this book" not in text


def test_message_confirms_processing_and_next_step():
    text = Instructions.photos_already_uploaded.lower()
    # Confirms successful processing...
    assert "processed" in text
    # ...mentions the automatic text read...
    assert "text" in text and "automatically" in text
    # ...and states the next step.
    assert "next step" in text


def test_message_formats_with_and_without_page_count():
    with_count = Instructions.photos_already_uploaded.format(count_str=" (14 pages)")
    assert "(14 pages)" in with_count
    without = Instructions.photos_already_uploaded.format(count_str="")
    assert "()" not in without
