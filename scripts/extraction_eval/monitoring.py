"""The frozen empty/empty cohort is monitoring data, not wordless truth."""

from .normalization import tokenize
from text_content.evaluation import EvaluationText, EvaluationErrors


def monitor_empty_reference(manifest, predictions, cohort, *, selected_ids=None):
    if manifest.get("snapshot_id") != cohort.get("snapshot_id"):
        raise ValueError(EvaluationErrors.monitoring_cohort_snapshot_identity_mismatch)
    pages = {
        (book.get("id"), page.get("id")): page
        for book in manifest.get("books", [])
        for page in book.get("pages", [])
    }
    rows = []
    for record in cohort.get("records", []):
        if selected_ids is not None and record.get("book_id") not in selected_ids:
            continue
        page = pages.get((record.get("book_id"), record.get("page_id")))
        if page is None or (page.get("image") or {}).get("sha256") != record.get(
            "image_sha256"
        ):
            raise ValueError(
                EvaluationErrors.monitoring_cohort_page_image_identity_mismatch
            )
        actual = (
            predictions.get("books", {})
            .get(record.get("book_id"), {})
            .get("pages", {})
            .get(record.get("page_id"), {})
        )
        completed = actual.get("status") == "completed"
        tokens = len(tokenize(actual.get("text", ""))) if completed else 0
        verified = record.get("verified_blank") is True
        rows.append(
            {
                "book_id": record.get("book_id"),
                "page_id": record.get("page_id"),
                "image_sha256": record.get("image_sha256"),
                "completed": completed,
                "verified_blank": verified,
                "newly_nonempty": completed and tokens > 0,
                "unreviewed_disagreement_tokens": tokens if not verified else 0,
                "confirmed_hallucinated_tokens": tokens if verified else 0,
            }
        )
    return {
        "version": cohort.get("version"),
        "qualification": EvaluationText.unreviewed,
        "original_empty_reference_pages": cohort.get("original_empty_reference_pages"),
        "verified_recovery_pages": cohort.get("verified_recovery_pages"),
        "intended_monitoring_pages": len(rows),
        "completed_monitoring_pages": sum(row["completed"] for row in rows),
        "newly_nonempty_pages": sum(row["newly_nonempty"] for row in rows),
        "unreviewed_disagreement_tokens": sum(
            row["unreviewed_disagreement_tokens"] for row in rows
        ),
        "confirmed_hallucinated_tokens": sum(
            row["confirmed_hallucinated_tokens"] for row in rows
        ),
        "verified_blank_page_records": sum(row["verified_blank"] for row in rows),
        "independent_verified_blank_images": len(
            {row["image_sha256"] for row in rows if row["verified_blank"]}
        ),
        "records": rows,
    }
