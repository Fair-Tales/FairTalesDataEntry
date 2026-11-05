"""Event-scoped bibliography-v2 scoring; unverified legacy fields are not gold."""

from __future__ import annotations

from text_content.evaluation import EvaluationErrors

PUBLICATION_SCORER_VERSION = "bibliography-scoring-v1"
EQUIVALENCE_VERSION = "publication-equivalence-v1"
TARGETS = ("photographed_edition", "first_publication")
FIELDS = ("publisher", "imprint", "publishing_house", "year")


def _target(metadata, name):
    record = metadata.get(name, {})
    if isinstance(record, dict) and isinstance(record.get("value"), dict):
        return (
            record["value"] if record.get("status") in {"observed", "verified"} else {}
        )
    return record if isinstance(record, dict) else {}


def _known(target, field):
    fallback = target.get("publisher_status") if field == "imprint" else None
    supported = any(
        isinstance(item, dict)
        and item.get("quote")
        and item.get("provenance") in {"photo", "frozen_lookup", "manual_review"}
        and (item.get("source_page_id") is not None or item.get("source_id"))
        and (
            item.get("image_hash")
            or item.get("image_sha256")
            or item.get("provenance") == "manual_review"
        )
        and item.get("field", field)
        in {field, "publication_event", "publisher" if field == "imprint" else field}
        for item in target.get("evidence", [])
    )
    return (
        target.get(field + "_status", fallback) in {"evidenced", "manually_verified"}
        and _value(target, field) is not None
        and supported
    )


def _value(target, field):
    return target.get(field, target.get("publisher") if field == "imprint" else None)


def _scope(target, name):
    return (
        "edition" if name == "photographed_edition" else target.get("scope", "unknown"),
        target.get("territory"),
    )


def _valid_scope(target, name):
    scope, territory = _scope(target, name)
    return scope in {"edition", "work_first"} or (
        scope == "territorial_first" and bool(territory)
    )


def score_publication(
    reference_metadata, predicted_metadata, *, status="completed", protocol=None
):
    protocol = protocol or {}
    table = protocol.get("publication_equivalences", {})
    if table and (not table.get("version") or table.get("reviewed") is not True):
        raise ValueError(
            EvaluationErrors.publication_equivalences_require_a_reviewed_versioned_event_scoped_table
        )
    targets = {}
    for name in TARGETS:
        reference, prediction = (
            _target(reference_metadata, name),
            _target(predicted_metadata, name),
        )
        same_scope = _valid_scope(reference, name) and _scope(
            reference, name
        ) == _scope(prediction, name)
        fields = {}
        for field in FIELDS:
            known = _known(reference, field) and _valid_scope(reference, name)
            asserted = (
                status == "completed"
                and _known(prediction, field)
                and _valid_scope(prediction, name)
            )
            expected, actual = _value(reference, field), _value(prediction, field)
            # Imprint strings are strictly literal, including case and punctuation.
            exact = bool(
                known
                and asserted
                and same_scope
                and type(actual) is type(expected)
                and actual == expected
            )
            if field == "year":
                exact = exact and type(actual) is int
            fields[field] = {
                "matched": int(exact),
                "reference": int(known),
                "agreement": int(exact) if known else None,
                "reference_value": expected,
                "prediction_value": actual,
                "prediction_status": status,
                "asserted": asserted,
                "known_fact_conflict": bool(known and asserted and not exact),
                "unknown_reference": not known,
                "scope_matches": same_scope,
                "unresolved": not asserted,
            }
        known_scope = _valid_scope(reference, name) and any(
            _known(reference, field) for field in FIELDS
        )
        for field in ("scope", "territory"):
            known = known_scope and (
                field != "territory" or reference.get("territory") is not None
            )
            expected = (
                _scope(reference, name)[0] if field == "scope" else reference.get(field)
            )
            actual = (
                _scope(prediction, name)[0]
                if field == "scope"
                else prediction.get(field)
            )
            matched = bool(
                known
                and status == "completed"
                and _valid_scope(prediction, name)
                and expected == actual
            )
            fields[field] = {
                "matched": int(matched),
                "reference": int(known),
                "agreement": int(matched) if known else None,
            }
        joint_known = all(fields[field]["reference"] for field in ("publisher", "year"))
        joint = joint_known and all(
            fields[field]["matched"] for field in ("publisher", "year")
        )
        fields["joint_event"] = {
            "matched": int(joint),
            "reference": int(joint_known),
            "agreement": int(joint) if joint_known else None,
        }
        publisher = fields["publisher"]
        equivalent = bool(publisher["matched"])
        if (
            publisher["reference"]
            and publisher["asserted"]
            and same_scope
            and not equivalent
        ):
            for mapping in table.get("records", []):
                if (
                    mapping.get("target") == name
                    and mapping.get("scope") == _scope(reference, name)[0]
                    and mapping.get("territory") == reference.get("territory")
                    and mapping.get("evidence")
                    and publisher["reference_value"] in mapping.get("names", [])
                    and publisher["prediction_value"] in mapping.get("names", [])
                ):
                    equivalent = True
                    break
        fields["publisher_equivalent"] = {
            "matched": int(equivalent),
            "reference": publisher["reference"],
            "agreement": int(equivalent) if publisher["reference"] else None,
        }
        targets[name] = fields
    return {
        "version": PUBLICATION_SCORER_VERSION,
        "metadata_schema_version": "bibliography-v2",
        "equivalence_version": table.get("version", EQUIVALENCE_VERSION),
        "equivalence_normalization": "literal-reviewed-names-v1",
        "targets": targets,
    }


def aggregate_publication(scores):
    result = {"version": PUBLICATION_SCORER_VERSION, "targets": {}}
    for target in TARGETS:
        fields = {}
        for field in (
            *FIELDS,
            "scope",
            "territory",
            "joint_event",
            "publisher_equivalent",
        ):
            records = [
                score.get("publication", {})
                .get("targets", {})
                .get(target, {})
                .get(field, {})
                for score in scores
            ]
            matched = sum(row.get("matched", 0) for row in records)
            reference = sum(row.get("reference", 0) for row in records)
            fields[field] = {
                "matched": matched,
                "reference": reference,
                "agreement": matched / reference if reference else None,
                "asserted_books": sum(row.get("asserted", False) for row in records),
                "unknown_reference_books": sum(
                    row.get("unknown_reference", False) for row in records
                ),
                "known_fact_conflicts": sum(
                    row.get("known_fact_conflict", False) for row in records
                ),
                "all_book_coverage": sum(row.get("asserted", False) for row in records)
                / len(scores)
                if scores
                else None,
            }
        result["targets"][target] = fields
    return result
