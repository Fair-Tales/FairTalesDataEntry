"""Offline book-cluster inference. Challenge books never estimate the population."""

from __future__ import annotations

import random
import math
from collections import defaultdict


def _quantile(values, probability):
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def weighted_mean(records, value="value"):
    usable = [record for record in records if record.get(value) is not None]
    probabilities = [record.get("inclusion_probability", 1.0) for record in usable]
    if any(probability is None or not 0 < probability <= 1 for probability in probabilities):
        raise ValueError("Inclusion probabilities must satisfy 0 < p <= 1")
    weights = [1 / probability for probability in probabilities]
    return (sum(record[value] * weight for record, weight in zip(usable, weights))
            / sum(weights)) if weights else None


def bootstrap_books(records, *, value="value", repeats=2000, seed=20261104):
    """Resample whole books within strata, preserving inverse-probability weights.

    Records require an id and scalar value; optional role, stratum and inclusion
    probability describe the frozen selection. Purposive challenges are excluded.
    These intervals describe sampled books, not reference validity/future accuracy.
    """
    if repeats < 1:
        raise ValueError("Bootstrap repeats must be positive")
    usable = [dict(record) for record in records
              if record.get(value) is not None and record.get("role") not in {"challenge", "explicit"}]
    for record in usable:
        probability = record.get("inclusion_probability", 1.0)
        if probability is None or not 0 < probability <= 1:
            raise ValueError("Probability sample requires 0 < inclusion_probability <= 1")
    groups = defaultdict(list)
    for record in usable:
        groups[record.get("stratum", "all")].append(record)
    rng = random.Random(seed)
    estimates = []
    for _ in range(repeats if usable else 0):
        drawn = [rng.choice(group) for group in groups.values() for _ in group]
        estimates.append(weighted_mean(drawn, value))
    return {"estimate": weighted_mean(usable, value), "books": len(usable),
            "interval_95": [_quantile(estimates, .025), _quantile(estimates, .975)]
            if estimates else None, "repeats": repeats, "seed": seed,
            "resampling_unit": "book", "strata": {key: len(group) for key, group in groups.items()},
            "singleton_strata": [key for key, group in groups.items() if len(group) == 1],
            "qualification": "Book sampling interval; does not establish reference correctness or future-book accuracy."}


def paired_comparison(baseline, candidate, *, value="value", repeats=2000, seed=20261104):
    """Candidate-minus-baseline book differences on identical reference identities."""
    left = {record["id"]: record for record in baseline}
    right = {record["id"]: record for record in candidate}
    if left.keys() != right.keys():
        raise ValueError("Paired comparison requires identical book ids")
    differences = []
    for book_id, before in left.items():
        after = right[book_id]
        if not before.get("reference_hash") or before.get("reference_hash") != after.get("reference_hash"):
            raise ValueError("Paired comparison requires identical eligible reference hashes")
        if before.get(value) is None or after.get(value) is None:
            continue
        for key in ("stratum", "role", "inclusion_probability"):
            if before.get(key) != after.get(key):
                raise ValueError("Paired comparison requires identical sampling metadata")
        differences.append({**before, "value": after[value] - before[value]})
    result = bootstrap_books(differences, repeats=repeats, seed=seed)
    result["direction"] = "candidate_minus_baseline"
    result["challenge_differences"] = [record for record in differences if record.get("role") == "challenge"]
    result["explicit_differences"] = [record for record in differences if record.get("role") == "explicit"]
    result["descriptive_paired_difference"] = sum(record["value"] for record in differences) / len(differences) if differences else None
    result["paired_books"] = len(differences)
    return result


def replicate_variation(records, *, value="value"):
    """Separate fresh-model replicate variation from between-book variation.

    Each row identifies id (book), replicate_id, reference_hash and a metric.
    Optional story_flags maps page IDs to bool/None and reports flips only where
    at least two completed flags differ. No best-response selection is performed.
    """
    grouped = defaultdict(list)
    for record in records:
        grouped[record["id"]].append(record)
    result = []
    for book_id, group in sorted(grouped.items()):
        identities = [record.get("replicate_id") for record in group]
        if None in identities or len(set(identities)) != len(identities):
            raise ValueError("Replicate ids must be present and unique per book")
        hashes = {record.get("reference_hash") for record in group}
        if None in hashes or len(hashes) != 1:
            raise ValueError("Replicates must share identical eligible references")
        values = [record[value] for record in group if record.get(value) is not None]
        mean = sum(values) / len(values) if values else None
        deviation = math.sqrt(sum((item - mean) ** 2 for item in values) / (len(values) - 1)) if len(values) > 1 else None
        flags = defaultdict(set)
        for record in group:
            for page_id, flag in record.get("story_flags", {}).items():
                if isinstance(flag, bool):
                    flags[page_id].add(flag)
        result.append({"id": book_id, "replicates": len(group), "scored_replicates": len(values),
                       "mean": mean, "sample_standard_deviation": deviation,
                       "range": [min(values), max(values)] if values else None,
                       "story_flip_pages": sorted(page_id for page_id, observed in flags.items() if len(observed) > 1)})
    return result
