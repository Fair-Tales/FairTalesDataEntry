# Book-details (metadata) accuracy test: design (17 November 2026)

Astra (gpt-6-astra), read-only, at Chris's request (DECISIONS 026), following Fable's screen-then-gate approach. Verbatim apart from link paths. Chris's answers to its two questions are recorded in DECISIONS 027.

**Measure metadata separately against scan-checked references.** Existing records can provide a useful agreement figure immediately, but cannot establish transcription accuracy.

This review changed no files, made no network/API calls and opened no Check records. Snapshot content was accessed only through the cohort loader, restricted to Iterate, with persistence suppressed for this read-only review.

The reference position is:

| Field | Observed availability among 55 Iterate books | Trustworthiness |
|---|---:|---|
| Title | 55 | Human spreadsheet origin; useful, but spelling and catalogue titles may differ from print. |
| Author credits | 55 | Spreadsheet origin; import retained one person in some multiple-credit cases. Lists are explicitly incomplete. |
| Illustrator credits | 52 | AI web lookup; neither independently verified nor necessarily complete. |
| Publisher | 55 | AI lookup sought the original UK imprint, which may differ from the photographed edition. |
| Year | 55 | Spreadsheet or AI lookup, intended as first publication; insufficient evidence of edition or territorial scope. |
| ISBN, edition, work ID | 0 each | Optional later additions, absent from these frozen records. |
| Scoped publication references | 0 | Neither photographed-edition nor first-publication targets are populated. |

These are **55-book observations**, not inferred counts for all 75. The remaining 20 are Check books and were not inspected. All 55 records identify their origin as `pilot_import`; calling them wholly human-entered would be misleading. See [reference construction](../scripts/extraction_eval/snapshot.py:362) and [import provenance](../scripts/import_pilot_data.py:1355).

All 55 Iterate books have Word-export references. They contain some bibliographic material: 36 include an ISBN label, 41 a publication-related label, and 18 an explicit credit phrase. Those counts establish potential reference material, **not correct metadata**. Typing omissions, line breaks and uncertain edition correspondence remain. The [bibliography helpers](../bibliography.py:169) preserve evidence and validate ISBNs; they do not create independent truth.

**The smallest trustworthy addition is one reference file**, keyed by existing book ID, containing checked values, source page numbers and field status: present, confirmed absent, unreadable or unchecked. Use Word text and stored records as starting points, then verify against the scanned cover/title/copyright pages before comparing predictions. Check references must remain behind the existing aggregate-only boundary.

Score as follows:

| Field | Proposed rule |
|---|---|
| Title | Report literal exact and normalised match separately. Normalise Unicode, case, whitespace and typographic punctuation; retain substantive words. |
| Authors and illustrators | Match names one-to-one within roles, ignoring order. Normalise typography; permit initials, pseudonyms or spelling variants only through a reviewed equivalence list. Report complete-set correctness plus missing/extra credits. Incomplete historical lists support known-credit recall only. |
| Publisher | Normalised match to the photographed-edition imprint. A parent company is not automatically equivalent. |
| ISBN | Remove separators, validate the check digit and canonicalise ISBN-10 to ISBN-13 using the existing helper. Separately score correctness for this edition; a valid ISBN can identify another edition. Preserve multiple printed ISBNs and qualifiers. |
| Edition | Normalised match to an explicit edition statement. The current joint schema lacks a dedicated edition field: record this coverage gap rather than infer edition from a date. |
| Year | Exact integer **and matching publication scope**. Keep photographed-edition and first-publication results separate; copyright dates are not interchangeable with either. |

Work ID is human-assigned linkage under decision 023, not transcribed content, so exclude it.

The headline should average each book’s fraction of correctly recovered, verified-present fields. Report per-field numerators/denominators, reference coverage and wholly correct books alongside it. Missing predictions and failed calls count as errors; missing references do not. Report invented values on confirmed-absent fields separately and guard against increases. This prevents improved scores through abstention or denominator changes.

Reuse [existing metadata scoring helpers](../scripts/extraction_eval/scoring.py:312), with a new scoring identity whenever rules or references change, and rescore both arms.

For tuning:

- Fix 12 Iterate control books, covering metadata difficulties; add up to six cases for the stated hypothesis. Run incumbent and candidate twice with identical images and cached upstream work.
- Drop only clear failures: repeated invalid output, persistent targeted failures with no benefit, or repeatable control losses without an offsetting gain. Small, mixed or uncertain differences go forward. Screens never establish improvement.
- Test promoted ideas on all 55 Iterate and 20 Check books: propose four fresh passes per arm on Iterate and eight on Check, in half-price batches. Apply decision 020’s improvement/no-deterioration principle, using metadata’s own repeatability—not character noise. Report panel and other Iterate results separately.
- Use purpose-specific tokens, such as `meta-screen-01-candidate-1` and `meta-gate-01-candidate-1`; verify fresh metadata calls and upstream replay. Full tests return to Chris under decision 026.

The paid evidence already supports free diagnostics on 30 Iterate books: metadata completed **30/30**, titles match **14/30 literally and 23/30 after Unicode/case/whitespace normalisation**; known author names match **19/30**, illustrator names **18/28**. Of 28 extracted ISBN occurrences, **27 pass validation**. These are agreement/validity results, not verified accuracy. Score initial and final outputs separately because the new arm performed late reconciliation. The old baseline’s 30 failures establish its missing prompt, not today’s fallback performance.

In [calls.json](../evaluation_runs/baseline-p1-v2/calls.json), **29 initial joint-metadata calls cost $1.34885**, averaging **$0.04651**. Thus one fresh call for each of 75 books projects to **$3.49 synchronous or $1.74 batch**, assuming cached page location. Uncached location adds approximately **$2.04/$1.02** respectively. Thirteen late rereads cost another $0.63868; exclude them from the one-call estimate. These are projections, not quotes.

I would cut new lookup services, fuzzy-match machinery and repeated story transcription.

**Open questions for Chris**

1. **Reference quality:** verify metadata against scans for all 75 books, enabling an accuracy claim (recommended), or report existing-record agreement now and defer verification?
2. **Repeatability:** estimate metadata variability from both arms’ proposed four/eight gate passes, avoiding separate calibration (recommended), or retain a separate 20-pass calibration, adding approximately $33 in batch after the first pass? Either needs metadata-specific thresholds before acceptance.
