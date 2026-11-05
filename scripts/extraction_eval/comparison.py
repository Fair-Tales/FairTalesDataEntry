"""Field-aware, versioned paired comparisons over offline per-book counts.

Every registered endpoint is emitted, including absent evidence. Ratios are
recomputed inside paired, stratified book-cluster draws, never by averaging
page/alias observations or dropping failed eligible predictions.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from copy import deepcopy

from text_content.evaluation import EvaluationText, EvaluationErrors
from .store import digest
from .statistics import _quantile

COMPARISON_VERSION = "field-aware-paired-v1"
SCORER_VERSION = "pilot-metrics-v3+alias-foundation-v1+bibliography-v2"
# Direction is explicitly separate from whether an endpoint supplies a gate.
METRIC_REGISTRY = {
    "known_variant_recall_v1": {"direction": 1, "target": 0.90, "non_regression": True},
    "alias_recall": {"direction": 1, "non_regression": True},
    "character_recall": {"direction": 1, "non_regression": True},
    "M1_groundedness": {"direction": 1, "target": 0.95, "non_regression": True},
    "M1_editorial_allowance": {"direction": 1},
    "M1_ungrounded_emissions": {"direction": -1},
    "M2_redundancy": {"direction": -1},
    "M2_lost_raw_surfaces": {"direction": -1, "target": 0},
    "M2_duplicate_occurrences": {"direction": -1, "target": 0},
    "M3_collision_incidence": {"direction": -1},
    "M3_unhandled_flags": {"direction": -1, "target": 0},
    "M3_unresolved_share": {"direction": -1, "non_regression": True},
    "M4_fixed_retrieval": {"direction": 1, "non_regression": True},
    "M4_fixed_ambiguity": {"direction": -1, "non_regression": True},
    "M4_correct_attribution": {"direction": 1},
    "M4_wrong_attribution": {"direction": -1},
    "false_merge_rate": {"direction": -1, "target": 0, "non_regression": True},
    "false_split_rate": {"direction": -1, "target": 0, "non_regression": True},
    "ocr_f1": {"direction": 1},
    "ocr_recall": {"direction": 1},
    "ocr_cer": {"direction": -1},
    "ocr_wer": {"direction": -1},
    "story_agreement": {"direction": 1},
    "story_recall": {"direction": 1},
    "page_output_coverage": {"direction": 1, "non_regression": True},
    "story_output_coverage": {"direction": 1, "non_regression": True},
    "character_output_coverage": {"direction": 1, "non_regression": True},
    "metadata_output_coverage": {"direction": 1, "non_regression": True},
    "context_failures_per_book": {"direction": -1, "non_regression": True},
    "context_fallback_rate": {"direction": -1, "non_regression": True},
    "metadata_reconciliation_failures_per_book": {
        "direction": -1,
        "non_regression": True,
    },
    "context_output_coverage": {"direction": 1, "non_regression": True},
    "metadata_reconciliation_output_coverage": {"direction": 1, "non_regression": True},
    "context_fallback_story_agreement": {"direction": 1},
    "metadata.title": {"direction": 1, "non_regression": True},
    "metadata.authors": {"direction": 1, "non_regression": True},
    "metadata.illustrators": {"direction": 1, "non_regression": True},
    "metadata.publisher_v1": {"direction": 1},
    "metadata.published_year_v1": {"direction": 1},
    "blank_regression": {"direction": -1, "target": 0},
    "recovery_regression": {"direction": -1, "target": 0},
    "rotation_fixture_regression": {"direction": -1, "target": 0},
    "case_regression": {"direction": -1, "target": 0},
}
for _target in ("photographed_edition", "first_publication"):
    for _field in (
        "publisher",
        "imprint",
        "publishing_house",
        "year",
        "scope",
        "territory",
        "joint_event",
        "publisher_equivalent",
    ):
        METRIC_REGISTRY[f"publication.{_target}.{_field}"] = {
            "direction": 1,
            "non_regression": True,
        }


def count(numerator=0, denominator=0, *, status="completed", reason=None):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "eligible": bool(denominator),
        "status": status,
        "undefined_reason": reason
        or (EvaluationText.undefined if not denominator else None),
    }


def comparison_protocol(protocol, manifest):
    from alias_pipeline import ALIAS_SCHEMA_VERSION, MATCHER_VERSION
    from conventions import CONVENTION_VERSION
    from alias_schema import CANONICAL_RULE_VERSION
    from .alias_metrics import GROUNDING_VERSION

    convention_version = protocol.get("convention_version", CONVENTION_VERSION)
    return {
        "scorer": SCORER_VERSION,
        "registry": COMPARISON_VERSION,
        "schema": [ALIAS_SCHEMA_VERSION, "bibliography-v2"],
        "convention_version": convention_version,
        "matcher_version": MATCHER_VERSION,
        "canonical_version": CANONICAL_RULE_VERSION,
        "grounding_version": GROUNDING_VERSION,
        "normalization_version": protocol.get(
            "normalization_version", "pilot-normalization-v1"
        ),
        "protocol_hash": digest(protocol),
        "overlay_hash": (manifest.get("adjudication") or {}).get("overlay_hash"),
        "field_scope": "same-event-scope-territory-v1",
    }


def book_comparison_record(score, *, protocol_identity):
    characters, foundation = (
        score.get("characters", {}),
        score.get("alias_foundation", {}),
    )
    char_status = foundation.get("output_coverage", {}).get(
        "character_status", "pending"
    )
    metrics = {
        name: count(reason=EvaluationText.evidence_unavailable)
        for name in METRIC_REGISTRY
    }
    for name, key in (
        ("known_variant_recall_v1", "variants"),
        ("alias_recall", "alias_recall"),
        ("character_recall", "entities"),
    ):
        record = characters.get(key, {})
        metrics[name] = count(
            record.get("matched", 0),
            record.get("reference", 0) if characters.get("eligible") else 0,
            status=char_status,
        )
    m1, m2, m3 = (foundation.get(key, {}) for key in ("M1", "M2", "M3"))
    for name, numerator in (
        ("M1_groundedness", "grounded"),
        ("M1_editorial_allowance", "editorial_allowances"),
    ):
        metrics[name] = count(
            m1.get(numerator, 0), m1.get("aliases", 0), status=char_status
        )
    metrics["M1_ungrounded_emissions"] = count(
        m1.get("ungrounded", 0),
        1 if char_status == "completed" else 0,
        status=char_status,
    )
    metrics["M2_redundancy"] = count(
        m2.get("affected_characters", 0),
        m2.get("characters", 0) if char_status == "completed" else 0,
        status=char_status,
    )
    for name, key in (
        ("M2_lost_raw_surfaces", "lost_raw_surfaces"),
        ("M2_duplicate_occurrences", "duplicate_occurrences"),
    ):
        integrity = score.get("projection_integrity", {})
        metrics[name] = count(
            integrity.get(key, 0),
            int(char_status == "completed" and key in integrity),
            status=char_status,
        )
    metrics["M3_collision_incidence"] = count(
        int(m3.get("affected_book", False)),
        int(char_status == "completed"),
        status=char_status,
    )
    metrics["M3_unhandled_flags"] = count(
        m3.get("unhandled_flags", 0),
        int(char_status == "completed"),
        status=char_status,
    )
    metrics["M3_unresolved_share"] = count(
        m3.get("unresolved_flagged_occurrences", 0),
        m3.get("candidate_occurrences", 0),
        status=char_status,
    )
    for name, key in (
        ("false_merge_rate", "false_merges"),
        ("false_split_rate", "false_splits"),
    ):
        evidence = foundation.get("M4", {}).get(key, {})
        metrics[name] = count(
            evidence.get("errors") or 0,
            evidence.get("eligible_pairs", 0),
            status=char_status,
        )
    pages = score.get("pages", [])
    for name, numerator, denominator in (
        ("ocr_recall", "matched_tokens", "reference_tokens"),
        ("ocr_cer", "character_errors", "reference_characters"),
        ("ocr_wer", "word_errors", "reference_tokens"),
    ):
        metrics[name] = count(
            sum(page.get("ocr", {}).get(numerator, 0) for page in pages),
            sum(page.get("ocr", {}).get(denominator, 0) for page in pages),
        )
    ocr = score.get("ocr", {})
    metrics["ocr_f1"] = count(
        2 * ocr.get("matched", 0), ocr.get("reference", 0) + ocr.get("predicted", 0)
    )
    stories = [page.get("story", {}) for page in pages]
    metrics["story_agreement"] = count(
        sum(row.get("tp", 0) + row.get("tn", 0) for row in stories),
        sum(row.get("eligible_pages", 0) for row in stories),
    )
    metrics["story_recall"] = count(
        sum(row.get("tp", 0) for row in stories),
        sum(
            row.get("tp", 0) + row.get("fn", 0) + row.get("failed_true", 0)
            for row in stories
        ),
    )
    metrics["page_output_coverage"] = count(
        sum(page.get("ocr", {}).get("completed", False) for page in pages), len(pages)
    )
    metrics["story_output_coverage"] = count(
        sum(row.get("completed", False) for row in stories), len(pages)
    )
    metrics["character_output_coverage"] = count(
        int(char_status == "completed"), 1, status=char_status
    )
    meta_status = score.get("metadata_diagnostics", {}).get("status", "pending")
    metrics["metadata_output_coverage"] = count(
        int(meta_status == "completed"), 1, status=meta_status
    )
    for stage_name, prefix in (
        ("story_context", "context"),
        ("metadata_reconciliation", "metadata_reconciliation"),
    ):
        stage = score.get("optional_stages", {}).get(stage_name, {})
        status = stage.get("status", "not_requested")
        metrics[f"{prefix}_failures_per_book"] = count(
            int(stage.get("failed", False)), 1, status=status
        )
        metrics[f"{prefix}_output_coverage"] = count(
            int(stage.get("completed", False)),
            int(stage.get("intended", False)),
            status=status,
        )
    context = score.get("optional_stages", {}).get("story_context", {})
    metrics["context_fallback_rate"] = count(
        int(context.get("fallback", False)),
        1,
        status=context.get("status", "not_requested"),
    )
    fallback = score.get("story_context_fallback", {})
    metrics["context_fallback_story_agreement"] = count(
        fallback.get("tp", 0) + fallback.get("tn", 0), fallback.get("eligible_pages", 0)
    )
    for field in ("title", "authors", "illustrators", "publisher", "published_year"):
        record = score.get("metadata_operational", score.get("metadata", {})).get(
            field, {}
        )
        name = (
            "metadata."
            + field
            + ("_v1" if field in {"publisher", "published_year"} else "")
        )
        if field in {"authors", "illustrators"}:
            metrics[name] = count(
                record.get("matched_credits", 0),
                record.get("reference_credits", 0),
                status=record.get("prediction_status", "pending"),
            )
        else:
            metrics[name] = count(
                int(record.get("exact") is True),
                int(record.get("exact") is not None),
                status=record.get("prediction_status", "pending"),
            )
    for target, fields in score.get("publication", {}).get("targets", {}).items():
        for field, record in fields.items():
            name = f"publication.{target}.{field}"
            if name in metrics:
                metrics[name] = count(
                    record.get("matched", 0),
                    record.get("reference", 0),
                    status=record.get("prediction_status", meta_status),
                )
    for name, record in score.get("regressions", {}).items():
        if name in metrics:
            metrics[name] = count(record.get("errors", 0), record.get("eligible", 0))
    return {
        "id": score.get("id"),
        "reference_hash": score.get("reference_hash"),
        "protocol_identity": protocol_identity,
        "metrics": metrics,
        "role": score.get("role", "descriptive"),
        "stratum": score.get("stratum", "all"),
        "inclusion_probability": score.get("inclusion_probability", 1.0),
        "holdout_group": score.get("holdout_group", score.get("id")),
        "ocr_f1": ocr.get("f1"),
        "alias_recall": characters.get("alias_recall"),
        "alias_grounding": m1,
        "m4": foundation.get("M4", {}),
        "character_input_hash": score.get("character_input_hash"),
    }


def _fixed_mentions(before, after):
    """Fix U to baseline source spans; changed OCR inputs are explicitly ineligible."""
    if not before.get("character_input_hash") or before.get(
        "character_input_hash"
    ) != after.get("character_input_hash"):
        for row in (before, after):
            for name in ("M4_fixed_retrieval", "M4_fixed_ambiguity"):
                row["metrics"][name] = count(
                    reason=EvaluationText.changed_character_inputs
                )
        return
    universe = before.get("m4", {}).get("occurrences", [])
    for row in (before, after):
        occurrences = row.get("m4", {}).get("occurrences", [])
        found = ambiguous = 0
        for source in universe:
            candidates = {
                candidate
                for occurrence in occurrences
                if occurrence.get("page_id") == source.get("page_id")
                for hit in occurrence.get("nested_hits", [occurrence])
                if hit.get("token_start", 0) >= source.get("token_start", 0)
                and hit.get("token_end", 0) <= source.get("token_end", 0)
                for candidate in hit.get("candidate_entity_ids", [])
            }
            found += bool(candidates)
            ambiguous += len(candidates) > 1
        row["metrics"]["M4_fixed_retrieval"] = count(found, len(universe))
        row["metrics"]["M4_fixed_ambiguity"] = count(ambiguous, len(universe))


def _ratio(rows, endpoint, *, weighted):
    totals = [0.0, 0.0]
    for row in rows:
        value = row["metrics"][endpoint]
        if not value["denominator"]:
            continue
        weight = 1 / row["inclusion_probability"] if weighted else 1
        totals[0] += weight * value["numerator"]
        totals[1] += weight * value["denominator"]
    return totals[0] / totals[1] if totals[1] else None


def compare_metrics(
    baseline, candidate, *, repeats=10000, seed=20261105, measurement_repair=False
):
    """Compare every endpoint; reject mixed scoring protocols before resampling.

    ``measurement_repair`` only relaxes reference/overlay identities for a
    labelled fixed-output raw-to-corrected delta, never a candidate comparison.
    """
    if repeats < 1:
        raise ValueError(EvaluationErrors.bootstrap_repeats_must_be_positive)
    if len({r.get("id") for r in baseline}) != len(baseline) or len(
        {r.get("id") for r in candidate}
    ) != len(candidate):
        raise ValueError(EvaluationErrors.duplicate_book_ids_in_paired_comparison)
    left, right = deepcopy(baseline), deepcopy(candidate)
    left.sort(key=lambda r: r.get("id", ""))
    right.sort(key=lambda r: r.get("id", ""))
    if [r.get("id") for r in left] != [r.get("id") for r in right]:
        raise ValueError(EvaluationErrors.paired_comparison_requires_identical_book_ids)
    for before, after in zip(left, right):
        if not before.get("reference_hash") or (
            not measurement_repair
            and before.get("reference_hash") != after.get("reference_hash")
        ):
            raise ValueError(
                EvaluationErrors.paired_comparison_requires_identical_eligible_reference_hashes
            )
        identities = [
            dict(row.get("protocol_identity") or {}) for row in (before, after)
        ]
        if measurement_repair:
            for identity in identities:
                identity.pop("overlay_hash", None)
        if not identities[0] or identities[0] != identities[1]:
            raise ValueError(
                EvaluationErrors.paired_comparison_requires_identical_schema_convention_matching_normalizer_and
            )
        for key in ("stratum", "role", "inclusion_probability", "holdout_group"):
            if before.get(key) != after.get(key):
                raise ValueError(
                    EvaluationErrors.paired_comparison_requires_identical_sampling_metadata
                )
        for row in (before, after):
            if set(row.get("metrics", {})) != set(METRIC_REGISTRY):
                raise ValueError(
                    EvaluationErrors.paired_comparison_requires_the_complete_named_metric_registry
                )
            for value in row["metrics"].values():
                if (
                    not isinstance(value.get("numerator"), (int, float))
                    or not isinstance(value.get("denominator"), (int, float))
                    or value["denominator"] < 0
                ):
                    raise ValueError(
                        EvaluationErrors.metric_numerator_denominator_must_be_finite_nonnegative_counts
                    )
                if (
                    not math.isfinite(value["numerator"])
                    or not math.isfinite(value["denominator"])
                    or value["numerator"] < 0
                ):
                    raise ValueError(
                        EvaluationErrors.metric_numerator_denominator_must_be_finite_nonnegative_counts
                    )
        _fixed_mentions(before, after)
    from .exposure import EXPOSED_IDS

    probabilistic = [
        i
        for i, row in enumerate(left)
        if row.get("role") in {"probability", "random"}
        and row.get("id") not in EXPOSED_IDS
    ]
    for i in probabilistic:
        probability = left[i].get("inclusion_probability")
        if not isinstance(probability, (int, float)) or not 0 < probability <= 1:
            raise ValueError(
                EvaluationErrors.probability_sample_requires_0_inclusion_probability_1
            )
    groups = defaultdict(lambda: defaultdict(list))
    resampled = probabilistic or [
        i
        for i, row in enumerate(left)
        if row.get("role") not in {"explicit", "challenge"}
    ]
    for i in resampled:
        row = left[i]
        groups[row.get("stratum", "all")][row.get("holdout_group") or row["id"]].append(
            i
        )
    cluster_strata = defaultdict(set)
    for stratum, clusters in groups.items():
        for group in clusters:
            cluster_strata[group].add(stratum)
    if any(len(strata) > 1 for strata in cluster_strata.values()):
        raise ValueError(
            EvaluationErrors.a_book_group_cannot_be_split_across_sampling_strata
        )
    rng = random.Random(seed)
    draws = []
    for _ in range(repeats if resampled else 0):
        draw = []
        for cluster_map in groups.values():
            clusters = list(cluster_map.values())
            draw.extend(i for _ in clusters for i in rng.choice(clusters))
        draws.append(draw)
    endpoints = {}
    selected = (
        probabilistic
        or resampled
        or [i for i, row in enumerate(left) if row.get("role") != "challenge"]
    )
    weighted = bool(probabilistic)
    for name, specification in METRIC_REGISTRY.items():
        a, b = [left[i] for i in selected], [right[i] for i in selected]
        before_value, after_value = (
            _ratio(a, name, weighted=weighted),
            _ratio(b, name, weighted=weighted),
        )
        sign = specification["direction"]
        difference = (
            sign * (after_value - before_value)
            if before_value is not None and after_value is not None
            else None
        )
        estimates = []
        undefined_draws = 0
        for draw in draws:
            first = _ratio([left[i] for i in draw], name, weighted=weighted)
            second = _ratio([right[i] for i in draw], name, weighted=weighted)
            if first is None or second is None:
                undefined_draws += 1
            else:
                estimates.append(sign * (second - first))
        interval = (
            [_quantile(estimates, 0.025), _quantile(estimates, 0.975)]
            if estimates
            else None
        )
        finite_interval = (
            _finite_interval(left, right, groups, name, difference, sign)
            if weighted and difference is not None
            else None
        )
        gate = None
        if difference is not None and (
            "target" in specification or specification.get("non_regression")
        ):
            gate = (not specification.get("non_regression") or difference >= 0) and (
                "target" not in specification
                or sign * (after_value - specification["target"]) >= 0
            )
        endpoints[name] = {
            "baseline": before_value,
            "candidate": after_value,
            "improvement": difference,
            "direction": "candidate_minus_baseline"
            if sign == 1
            else "baseline_minus_candidate",
            "interval_95": interval,
            "finite_population_interval_95": finite_interval,
            "undefined_bootstrap_draws": undefined_draws,
            "status": "undefined"
            if difference is None
            else "deterioration"
            if interval and interval[1] < 0
            else "unconfirmed"
            if not interval or interval[0] <= 0
            else "positive_exploratory_difference",
            "point_gate_passed": gate,
            "point_gate": specification,
            "baseline_eligible_books": sum(
                row["metrics"][name]["denominator"] > 0 for row in left
            ),
            "candidate_eligible_books": sum(
                row["metrics"][name]["denominator"] > 0 for row in right
            ),
            "per_book": [
                {
                    "id": x["id"],
                    "baseline": x["metrics"][name],
                    "candidate": y["metrics"][name],
                    "role": x.get("role"),
                    "stratum": x.get("stratum"),
                    "inclusion_probability": x.get("inclusion_probability"),
                }
                for x, y in zip(left, right)
            ],
            "challenge": _descriptive(left, right, name, "challenge", sign),
            "explicit": _descriptive(left, right, name, "explicit", sign),
        }
    return {
        "version": COMPARISON_VERSION,
        "scorer_version": SCORER_VERSION,
        "kind": "fixed_output_measurement_repair"
        if measurement_repair
        else "paired_candidate_comparison",
        "qualification": EvaluationText.exploratory,
        "alias_smoke_test": EvaluationText.alias_smoke,
        "reference_precision": EvaluationText.precision,
        "m4_qualification": EvaluationText.m4,
        "finite_population_method": EvaluationText.finite_population,
        "sampling": "probability" if probabilistic else "descriptive_only",
        "interval_scope": EvaluationText.interval_scope,
        "paired_books": len(left),
        "repeats": repeats,
        "seed": seed,
        "resampling_unit": "book_or_declared_group",
        "metrics": endpoints,
    }


def _descriptive(left, right, name, role, sign):
    before = [row for row in left if row.get("role") == role]
    after = [row for row in right if row.get("role") == role]
    a, b = _ratio(before, name, weighted=False), _ratio(after, name, weighted=False)
    return {
        "books": len(before),
        "baseline": a,
        "candidate": b,
        "improvement": sign * (b - a) if a is not None and b is not None else None,
    }


def _finite_interval(left, right, groups, name, difference, sign):
    """Linearized stratified ratio difference, retaining baseline/candidate covariance."""
    all_ids = [
        i for clusters in groups.values() for ids in clusters.values() for i in ids
    ]
    totals = []
    for rows in (left, right):
        denominator = sum(
            rows[i]["metrics"][name]["denominator"] / rows[i]["inclusion_probability"]
            for i in all_ids
        )
        estimate = _ratio([rows[i] for i in all_ids], name, weighted=True)
        if not denominator or estimate is None:
            return None
        totals.append((denominator, estimate))
    variance = 0.0
    for cluster_map in groups.values():
        clusters = list(cluster_map.values())
        probabilities = {
            left[i]["inclusion_probability"] for ids in clusters for i in ids
        }
        if len(probabilities) != 1:
            return None
        probability = probabilities.pop()
        if probability == 1:
            continue
        if len(clusters) < 2:
            return None
        residuals = []
        for ids in clusters:
            residual = 0.0
            for i in ids:
                for side, rows in enumerate((left, right)):
                    record = rows[i]["metrics"][name]
                    if not record["denominator"]:
                        continue
                    denominator, estimate = totals[side]
                    residual += (
                        (-1 if side == 0 else 1)
                        * (record["numerator"] - estimate * record["denominator"])
                        / (denominator * probability)
                    )
            residuals.append(sign * residual)
        mean = sum(residuals) / len(residuals)
        variance += (
            (1 - probability)
            * len(residuals)
            * sum((value - mean) ** 2 for value in residuals)
            / (len(residuals) - 1)
        )
    width = 1.96 * math.sqrt(variance)
    return [difference - width, difference + width]
