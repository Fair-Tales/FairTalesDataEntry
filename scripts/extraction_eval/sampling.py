"""Deterministic book sampling with a frozen, replayable candidate inventory."""
from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .snapshot import _atomic_json
from .exposure import EXPOSED_IDS, exposure_ledger

SELECTION_VERSION = "pilot-book-selection-v1"


def _resolve(values, candidates):
    result = set()
    for value in values or []:
        matches = [row.get("id") for row in candidates
                   if value in {row.get("id"), row.get("title")}]
        if len(matches) != 1:
            raise ValueError(f"Book must resolve to one eligible id: {value!r} ({len(matches)} matches)")
        result.add(matches[0])
    return result


def _inventory(manifest):
    rows = []
    excluded = []
    for book in sorted(manifest.get("books", []), key=lambda b: b.get("id", "")):
        pages = book.get("pages", [])
        image_count = sum(bool(p.get("image")) for p in pages)
        if not image_count:
            excluded.append({"id": book.get("id"), "reason": "no_unambiguous_original_photos"})
            continue
        observed_texts = [p.get("reference", {}).get("text", {}) for p in pages
                          if p.get("reference", {}).get("text", {}).get("status") in {"observed", "verified"}]
        nonempty = [r.get("value") for r in observed_texts if isinstance(r.get("value"), str) and r.get("value").strip()]
        density = sum(len(text.split()) for text in nonempty) / image_count
        density_category = "unknown" if not nonempty else "low" if density < 20 else "medium" if density < 60 else "high"
        rows.append({"id": book.get("id"), "title": book.get("title", ""),
                     "page_count": len(pages), "image_count": image_count,
                     "observed_words_per_image": density, "text_density": density_category,
                     "wordless_reference_pages": sum(r.get("kind") == "wordless" for r in observed_texts),
                     "character_count": len(book.get("reference", {}).get("characters", {}).get("value") or []),
                     "explicit_alias_count": sum(len(character.get("aliases") or []) for character in
                                                 (book.get("reference", {}).get("characters", {}).get("value") or [])),
                     "recorded_story_text": any(page.get("reference", {}).get("contains_story", {}).get("value") is True
                                                and bool(page.get("reference", {}).get("text", {}).get("value")) for page in pages)})
    counts = sorted(row.get("image_count") for row in rows)
    cuts = [counts[min(len(counts) - 1, int(len(counts) * fraction))]
            for fraction in (0.25, 0.5, 0.75)] if counts else []
    for row in rows:
        quantile = sum(row.get("image_count") > cut for cut in cuts) + 1
        row["stratum"] = f"pages_q{quantile}:density_{row.get('text_density')}"
    duplicate_groups = _holdout_groups(rows, manifest)
    return rows, excluded, duplicate_groups


def _holdout_groups(rows, manifest):
    """Conservatively protect held-out books sharing titles or original photos.

    Automatic evidence is deliberately limited to frozen source data. It cannot
    identify every near-identical edition; the saved inventory remains subject
    to manual split review before tuning. Unselected members of a touched group
    are excluded from held-out claims, without enlarging the paid subset.
    """
    books = {book.get("id"): book for book in manifest.get("books", [])}
    parent = {row.get("id"): row.get("id") for row in rows}

    def root(book_id):
        while parent.get(book_id) != book_id:
            book_id = parent.get(book_id)
        return book_id

    def title_key(value):
        return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value or "").casefold()))

    hashes = {row.get("id"): {(page.get("image") or {}).get("sha256")
              for page in books.get(row.get("id"), {}).get("pages", [])
              if (page.get("image") or {}).get("sha256")} for row in rows}
    evidence = []
    for position, left in enumerate(rows):
        left_id = left.get("id")
        for right in rows[position + 1:]:
            right_id = right.get("id")
            a, b = hashes.get(left_id, set()), hashes.get(right_id, set())
            shared = len(a & b)
            title = title_key(left.get("title"))
            same_title = bool(title and title == title_key(right.get("title")))
            same_images = bool(a and b and a == b)
            shared_images = shared >= 3 and shared / min(len(a), len(b)) >= .8
            declared_left = books.get(left_id, {}).get("holdout_group")
            declared_right = books.get(right_id, {}).get("holdout_group")
            declared = bool(declared_left and declared_left == declared_right)
            if same_title or same_images or shared_images or declared:
                a_root, b_root = sorted((root(left_id), root(right_id)))
                parent[b_root] = a_root
                evidence.append({"book_ids": [left_id, right_id], "same_title": same_title,
                                 "identical_image_hash_sets": same_images,
                                 "shared_image_hash_count": shared, "declared_group": declared})
    groups = defaultdict(list)
    for row in rows:
        groups[root(row.get("id"))].append(row.get("id"))
    result = []
    group_by_id = {}
    for ids in groups.values():
        group = "group-" + hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:16]
        group_by_id.update({book_id: group for book_id in ids})
        if len(ids) > 1:
            result.append({"holdout_group": group, "book_ids": sorted(ids),
                           "evidence": [item for item in evidence if set(item.get("book_ids", [])) <= set(ids)]})
    for row in rows:
        row["holdout_group"] = group_by_id.get(row.get("id"))
    return sorted(result, key=lambda group: group.get("holdout_group"))


def select_books(manifest, limit=None, books=None, seed=20261104, challenge_books=None):
    """Return a JSON selection; explicitly supplied books are purposive selections.

    With no explicit ids, allocate probability-sample slots across page-count /
    observed-text-density strata. Challenge ids are added separately and receive
    no population inclusion probability. Empty text alone means unknown density,
    never a claim that a book is picture-only. ``limit`` counts complete books.
    """
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("limit must be a positive book count")
    candidates, excluded, duplicate_groups = _inventory(manifest)
    explicit = _resolve(books, candidates)
    challenge = _resolve(challenge_books, candidates)
    if explicit and not challenge.issubset(explicit):
        raise ValueError("Challenge books must be included in an explicit --books selection")
    if explicit and limit is not None and limit < len(explicit):
        raise ValueError("--limit is smaller than the explicit --books selection")
    target = len(explicit) if explicit else min(limit if limit is not None else len(candidates), len(candidates))
    if len(challenge) > target:
        raise ValueError("Challenge count exceeds the requested number of books")
    rng = random.Random(seed)
    selected = []
    by_id = {row.get("id"): row for row in candidates}

    def add(book_id, role, probability):
        row = by_id.get(book_id, {})
        selected.append({**row, "role": role, "inclusion_probability": probability})

    if explicit:
        method = "explicit_purposive"
        for book_id in sorted(explicit):
            add(book_id, "challenge" if book_id in challenge else "explicit", None)
    else:
        for book_id in sorted(challenge):
            add(book_id, "challenge", None)
        remainder = [row for row in candidates if row.get("id") not in challenge]
        slots = target - len(challenge)
        strata = defaultdict(list)
        for row in remainder:
            strata[row.get("stratum")].append(row.get("id"))
        if slots < len(strata):
            # Too few slots to cover every stratum: SRS still gives every book a
            # known nonzero probability, unlike deterministically omitting strata.
            method = "simple_random_insufficient_slots_for_all_strata"
            ids = [row.get("id") for row in remainder]
            for book_id in rng.sample(ids, slots):
                add(book_id, "probability", slots / len(ids))
        else:
            method = "stratified_random"
            quotas = {stratum: 1 for stratum in strata}
            left = slots - len(quotas)
            while left:
                eligible = [s for s in sorted(strata) if quotas.get(s, 0) < len(strata.get(s, []))]
                if not eligible:
                    break
                # Deterministic balanced proportional allocation, before draws.
                chosen = max(eligible, key=lambda s: len(strata.get(s, [])) / (quotas.get(s, 0) + 1))
                quotas[chosen] += 1
                left -= 1
            for stratum in sorted(strata):
                ids, count = strata.get(stratum, []), quotas.get(stratum, 0)
                for book_id in rng.sample(ids, count):
                    add(book_id, "probability", count / len(ids))
    selected.sort(key=lambda row: row.get("id", ""))
    selected_ids = {row.get("id") for row in selected}
    touched_groups = {row.get("holdout_group") for row in selected}
    exposed_groups = {row.get("holdout_group") for row in candidates if row.get("id") in EXPOSED_IDS}
    quarantined_groups = touched_groups | exposed_groups
    held_out_exclusions = [{"id": row.get("id"), "holdout_group": row.get("holdout_group"),
                            "reason": "previous_content_exposure" if row.get("holdout_group") in exposed_groups else "duplicate_group_contains_selected_book"}
                           for row in candidates if row.get("id") not in selected_ids
                           and row.get("holdout_group") in quarantined_groups]
    selection = {"version": SELECTION_VERSION, "snapshot_id": manifest.get("snapshot_id"),
                 "seed": seed, "requested_limit": limit, "method": method,
                 "book_ids": [row.get("id") for row in selected], "selected": selected,
                 "inventory": candidates, "excluded": excluded,
                 "challenge_books": sorted(challenge),
                 "duplicate_groups": duplicate_groups,
                 "held_out_book_ids": [row.get("id") for row in candidates
                                       if row.get("holdout_group") not in quarantined_groups],
                 "exposure_ledger": exposure_ledger(selected_ids),
                 "held_out_exclusions": held_out_exclusions,
                 "page_count": sum(row.get("image_count", 0) for row in selected),
                 "probability_sample_count": sum(row.get("role") == "probability" for row in selected),
                 "challenge_count": len(challenge),
                 "notes": ["Challenge and explicit selections are descriptive, not probability estimates.",
                           "Unselected members of a selected duplicate group are excluded from held-out claims.",
                           "Automatic grouping uses normalized titles or shared original image hashes; review other near-identical editions and challenge coverage before tuning."]}
    selection["selection_id"] = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    return selection


def save_selection(path, selection):
    """Atomically create a frozen selection; never replace a different split."""
    path = Path(path)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != selection:
            raise ValueError("Selection is immutable; choose a new run directory for a different split")
        return existing
    _atomic_json(path, selection)
    return selection


def select_holdout_books(manifest, *, alias_positive=27, alias_zero=13, seed=20261105):
    """Freeze §8's two-stratum sample using metadata/counts only; perform no reads.

    This constructs a plan, never executes it. Counts are recomputed from the
    supplied immutable inventory; no unseen prose is returned in the selection.
    """
    candidates, excluded, duplicate_groups = _inventory(manifest)
    exposed_groups = {row.get("holdout_group") for row in candidates if row.get("id") in EXPOSED_IDS}
    representatives = {}
    for row in candidates:
        if row.get("holdout_group") in exposed_groups or not row.get("recorded_story_text"):
            continue
        representatives.setdefault(row.get("holdout_group"), row)
    strata = {"alias_positive": [], "alias_zero": []}
    for row in representatives.values():
        strata["alias_positive" if row.get("explicit_alias_count") else "alias_zero"].append(row)
    rng = random.Random(seed)
    selected = []
    for label, count in (("alias_positive", alias_positive), ("alias_zero", alias_zero)):
        population = sorted(strata[label], key=lambda row: row.get("id", ""))
        if type(count) is not int or count < 0 or count > len(population):
            from text_content.evaluation import EvaluationErrors
            raise ValueError(EvaluationErrors.holdout_allocation)
        for row in rng.sample(population, count):
            selected.append({**row, "reporting_stratum": row.get("stratum"), "stratum": label,
                             "role": "probability", "inclusion_probability": count / len(population),
                             "population_stratum_size": len(population)})
    selected.sort(key=lambda row: row.get("id", ""))
    selected_ids = {row["id"] for row in selected}
    result = {"version": "pilot-holdout-alias-strata-v1", "snapshot_id": manifest.get("snapshot_id"),
              "method": "stratified_random_alias_presence", "seed": seed,
              "book_ids": sorted(selected_ids), "selected": selected,
              "eligible_counts": {label: len(rows) for label, rows in strata.items()},
              "inventory": list(representatives.values()), "excluded": excluded,
              "duplicate_groups": duplicate_groups, "exposure_ledger": exposure_ledger(),
              "remaining_unexposed_book_ids": sorted(row["id"] for row in representatives.values() if row["id"] not in selected_ids),
              "exposed_after_unblinding": exposure_ledger(selected_ids),
              "page_count": sum(row.get("image_count", 0) for row in selected),
              "challenge_count": 0, "probability_sample_count": len(selected)}
    books = {book.get("id"): book for book in manifest.get("books", [])}
    for row in selected:
        book = books.get(row["id"], {})
        row["reference_hash"] = hashlib.sha256(json.dumps({"pages": [{"id": page.get("id"), "reference": page.get("reference")} for page in book.get("pages", [])], "reference": book.get("reference", {})}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result["selection_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
