I recommend a standalone, snapshot-based evaluator under `scripts/extraction_eval/`, with three separate operations: **read production data, run extraction into local storage, and score cached results offline**.

I verified `claude-dev` at `90d0198`. No files were changed, and no Firestore, S3, or Anthropic requests were made. The corpus counts and empirical costs below remain your supplied measurements.

There is one substantial qualification: **the code does not establish all imported Firestore fields as independent student ground truth.** The harness should initially report *agreement with the imported pilot reference*, with a separate accuracy report for independently verified annotations.

The [importer’s source description](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/scripts/import_pilot_data.py:9) establishes that:

| Reference field | Provenance indicated by the code | Evaluation consequence |
|---|---|---|
| Title, author, publication year | Pilot spreadsheet; some year values have AI fallback | Preserve provenance and distinguish first-publication year from photographed-edition year. |
| Page text | PDF text layer or Claude OCR, followed by optional AI cleaning | Errors may correlate with the evaluated pipeline. |
| Human-validated text | Separate whole-book dataframe, used only for an import cross-check | Valuable independent reference if available; it does not supply page alignment automatically. |
| `contains_story` | Spreadsheet story-page range; missing range defaults all pages to story | Evaluate the annotation convention, including wordless narrative pages. |
| Illustrator/publisher | AI lookup during import | Agreement is not independent verification. |
| Characters/aliases | Pilot SQLite records | Stronger reference, subject to import collisions and annotation completeness. |
| Themes | No pilot annotations; application defaults can become `False` | Do not score these defaults as negative ground truth. |

These mappings are documented in [PILOT_IMPORT.md](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/scripts/PILOT_IMPORT.md:174). `validated_by='pilot_import'` is an assigned value, not evidence of subsequent human review.

**Reference eligibility must be decided before examining model predictions.** Each field/page gets a status such as `observed`, `verified`, `missing`, `ambiguous`, or `not_annotated`, alongside its raw value and provenance.

For your known empty-text problem:

- Mark every unreviewed `contains_story=True` page with empty text as `unknown_missing_transcription`. Exclude it from OCR denominators, retain its prediction for inspection, and report excluded page and book counts.
- Do not exclude the book’s other usable pages.
- Human inspection can change the status to `verified_wordless` or supply a transcription in a versioned local adjudication file.
- Keep story classification independently eligible when its label is credible. Missing transcription does not imply `contains_story=False`.
- Show the approximately 17 affected books as a named reporting subgroup, including the three examples you supplied.

There is another empty-text trap: the importer [deliberately blanks non-story text](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/scripts/import_pilot_data.py:2404), while today’s OCR prompt transcribes front/back matter. Empty copyright/title-page references therefore cannot establish either OCR correctness or hallucination.

The scoring functions should be pure functions over these reference records and cached predictions:

| Function | Definition and reporting |
|---|---|
| `score_ocr(reference, prediction)` | Primary: normalized **token-multiset precision, recall and F1**. Secondary: normalized character error rate and word error rate, both order-sensitive. |
| `score_story(reference, prediction)` | Positive class is story. Return TP/FP/FN/TN, explicit failed/abstained predictions, precision, recall, F1, accuracy and coverage. |
| `score_metadata(field, reference, prediction)` | Field-specific normalized exact matching; retain continuous fuzzy similarity and reviewed equivalences separately. |
| `match_characters(reference, prediction)` | Book-local, one-to-one entity matching using primary names and aliases. |
| `score_characters(matches)` | Entity precision/recall/F1, followed by correctly attached name/alias-variant precision/recall/F1. |
| `aggregate(scores, protocol)` | Book-level and corpus summaries, denominators, exclusions, failure rates, subgroups and statistical comparisons. |

For OCR, normalize Unicode, case and whitespace; canonicalize apostrophes and use a documented punctuation-insensitive tokenizer. Preserve numbers, diacritics and repeated words. Do not stem words or remove stop words. Handle uncertainty markers explicitly: `[?]` must not disappear into a perfect score.

For reference token counts \(G_w\) and predicted counts \(P_w\):

\[
M=\sum_w\min(G_w,P_w),\qquad
F_1=\frac{2M}{\sum_wG_w+\sum_wP_w}.
\]

This tolerates punctuation/case/spacing changes and reversed left/right text blocks on a spread. Repeated words still matter: extracting one occurrence of “very” when two were printed loses recall. Extra invented text loses precision.

Its limitation is explicit: **this measures recovered word content, not correct reading order or sentence meaning.** Retain ordered CER/WER and show disagreements where token F1 is high but ordered error is high. CER/WER can exceed 100% with insertions; do not silently clip them.

Compute matches within each photographed page/spread. Do not pool adjacent photos before matching, because that can hide missing or misplaced pages. Pool only through an explicit, reviewed alignment when the reference and photograph units differ.

Verified wordless pages get a separate “invented text” rate and token count; empty/empty pages should not inflate the primary text-recovery score. An extraction failure on an eligible nonempty reference produces zero recovered content and remains a recorded failure.

For `contains_story`, retain a 2×3 table: reference true/false against predicted true/false/failure. Report conventional precision/recall on valid predictions, plus coverage and end-to-end recall counting failures on true-story pages as misses. End-to-end accuracy counts every failed prediction as incorrect. This prevents a system that skips difficult pages from looking excellent.

Resolve the wordless-page convention before freezing the protocol: the [current prompt](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/text_content/ai_prompts.py:469) describes story pages as containing narrative text but subsequently labels a wordless illustration as story. Report imported-label agreement immediately; call adjudicated results classification accuracy only after that ambiguity is resolved.

For metadata:

- Resolve each DocumentReference to its snapshotted entity value. Authors use `forename`/`surname`; illustrator/publisher normally use `name`, with legacy-name fallback. Compare names, not document IDs.
- Preserve dangling references, `Unknown`, absent values and `published=-1` as missing reference information.
- Use normalized exact title/publisher/name agreement as the strict result. Also report normalized character-edit similarity for title/publisher and review candidates for near matches.
- Accept initials, alternative spellings and imprint equivalences only through a versioned, reviewed equivalence table. Matching surnames alone is insufficient.
- Compare author/illustrator credits as sets when complete reference credits exist. The importer can retain only one credited author; additional correct predictions must not automatically become false positives against an incomplete list.
- Compare `published_year` to stored `published` as an integer. Score exact agreement; show year differences diagnostically. First publication versus edition date needs adjudication, not an arbitrary ±1-year allowance.

For characters, represent each entity as its primary name plus aliases, preserving attachment to the entity. Match entities one-to-one within each book, using exact normalized variant overlap and reviewed equivalences. Duplicate predicted entities cannot both match one reference character.

Apply the existing [article-stripping rule](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/utilities.py:1919) symmetrically: remove one initial `the/a/an/my/your/his/her/its/our/their`. Thus “the narrator” and “narrator” agree. It does **not** strip arbitrary possessive nouns: “Tom’s mum” remains distinct. Preserve original forms and flag collisions such as “his mum” and “her mum”; do not merge entities merely because normalization collides.

After entity matching, compare `{primary name} ∪ aliases` so choosing a different canonical name is not penalized. Alias credit requires attachment to the correct entity. Report unmatched entities and their variants as misses/extras; ambiguous entity matches require review or explicit conservative/optimistic bounds.

Empty character lists are not automatically verified absence. Import documentation identifies same-name collisions, and alias IDs can also collide. Character precision therefore needs an annotation-completeness qualification.

The primary character run must consume **predicted** story flags and OCR text. The existing detector is text-only, so report visual-only annotated characters separately. A character run using independently verified text is a useful diagnostic of detection quality, but must be labelled separately from end-to-end performance.

The execution boundary needs a small production refactor before paid evaluation. Calling the background worker with different document names is insufficient:

- [Phase A/B/C](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/background_pipeline.py:259) writes S3 objects and pipeline-job records.
- [extract_page_info](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/pages/uploader.py:124) writes extraction-error records. Its computational core is `attempt_page_extraction`.
- Shared AI helpers call [_log_usage](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/utilities.py:446), which writes production `api_usage`.
- Even [Book.get_character_dict()](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/data_structures/book.py:412) can repair database membership.

Use raw Firestore documents during snapshotting, never application model objects. Give the snapshot command IAM-enforced read-only Firestore/S3 credentials. Inference should receive local images, explicit settings and local storage only; scoring should initialize no cloud clients.

The proposed data flow is:

```text
Firestore + original S3 images, read-only
                 |
          immutable snapshot
           /             \
reference + provenance    ordered image bytes
           |               |              |
           |          metadata       EXIF/correction
           |          + ISBN lookup       |
           |               |             OCR
           |               |              |
           |               |      predicted story text
           |               |              |
           |               |          characters
           |                \            /
           |             local predictions
           |           + raw response cache
            \                  /
                 offline scoring
                       |
            reports + paired comparisons
```

Use `s3_constants.S3_BUCKET`, `book_folder_name`, `is_page_image` and `page_image_number`. Select canonical `page_N.jpg` inputs; existing `_cropped`/`_display` objects are prior outputs, not interchangeable originals. Record actual page numbers, image hashes and object versions where available. Audit gaps, duplicates, mismatched document IDs and ambiguous book folders rather than silently renumbering.

Snapshot referenced entities, original fields, update times and available import diagnostics: `text_source`, `clean_status`, `ocr_model`, `clean_model`, `continuity`, review flags, `corrected`, and `rotation_uncertain`. Preserve absent fields as absent. Recheck source versions at snapshot completion and flag concurrent changes.

Metadata is actually separate from the worker phases. [extract_photo_first_metadata](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/utilities.py:2187) locates title/copyright pages, extracts metadata and can call Google Books by ISBN. Preserve that behavior: use original ordered photos without gold title-page hints, inject frozen settings, cache lookup responses, and retain value-level source attribution. Validate provider request limits for the largest books; any chunking change is a new pipeline variant.

The implementation can be organized as follows:

| Proposed file/module | Responsibility |
|---|---|
| `ai_runtime.py` | Streamlit-free shared request construction/parsing with injected client and usage sink. |
| `extraction_core.py` | Shared OCR, metadata and character computation; explicit settings and ISBN lookup dependency. Existing app entry points remain wrappers. |
| `scripts/extraction_eval/cli.py`, `__main__.py` | Commands and configuration validation. |
| `snapshot.py` | Read-only source export, reference resolution, integrity/provenance audit. |
| `sampling.py` | Seeded sampling and immutable split manifests. |
| `runner.py` | Production stage orchestration with local artifacts and stage statuses. |
| `gateway.py`, `store.py` | Cached requests, transactional spend ledger, retry policy and SQLite state. |
| `scoring.py`, `normalization.py` | Pure scoring, matching and versioned normalization. |
| `reporting.py`, `statistics.py` | Inspection reports, aggregates, bootstrap and paired comparisons. |
| `README.md`, `protocol.json` | Execution instructions and frozen evaluation choices. |
| `tests/test_extraction_eval_*.py` | Offline fixtures and failure-injection tests. |

Keep real photos, texts and responses under a configurable research-data output directory outside Git. Store immutable manifests/configuration, `eval.sqlite`, content-addressed images, raw responses, adjudication overlays and generated reports.

Reuse the rotation evaluator’s [seeded manifest pattern](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/scripts/rotation_prompt_eval/rot_sample.py:89), response reuse, exclusions and error inspection. Strengthen its cache design: [rot_eval.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/scripts/rotation_prompt_eval/rot_eval.py:106) stores only parsed answers and overwrites a JSON file. Its duplicated rotation algorithm also differs from current production. Share production computations instead of copying that algorithm.

Cache at **every request boundary**, including conditional rotation, crop checks and metadata location. A request identity includes exact image/content hashes, ordered messages, prompts, model, generation parameters, API options and replicate ID. Store raw response blocks, usage, request ID, stop reason and errors before parsing.

Keep these versions separate:

- Request version: determines whether another paid response is needed.
- Parser/stage version: permits reinterpretation of cached responses and invalidates dependent stage artifacts.
- Reference/metric version: changes scoring without paid calls.

Use transactional SQLite updates and atomic artifact writes. Stage state should distinguish pending, dispatched, completed, failed, budget-stopped and unknown-outcome calls. Resume completed work automatically; do not automatically repeat permanent parse failures or ambiguously completed requests.

For the initial subset, use **20 complete books**, not isolated pages:

- Sixteen seeded, stratified random selections spanning page-count quantiles and text-density categories.
- Four explicit challenge books: your three named gap examples plus a verified rotation/crop problem.
- Check coverage of wordless narrative pages, text-heavy books, spreads, decorative text and character-rich books. Empty imported text alone must not classify a book as picture-only.

Persist selected IDs, candidate inventory, stratum, selection role and inclusion probabilities. A seed alone is insufficient when the database changes. Report purposive challenge results separately from the probability sample.

Keep the remaining books held out during tuning; group duplicate editions or near-identical books together across splits. After freezing the pipeline and metrics, run the remaining pilot. Report held-out results separately from the full 195-book descriptive result, which includes books used for tuning.

Repeatability means reproducible inputs and replay, not identical fresh model answers. Freeze code revision, dependency versions, settings, prompts, model identifiers, source hashes and lookup responses. Preserve production’s existing generation parameters in the baseline; adding temperature zero is a named experiment. Run three independent replicates on four varied development books to measure score variation and classification flips. Replicates must have distinct cache identities, and results must never be selected by “best response.”

Cost control needs both planning estimates and admission control. Your empirical planning formula is:

\[
\widehat C=2(0.0111P+0.0103B),
\]

giving approximately $16 for 20 average-length books and $157.06 for 6,894 pages/195 books. Recalculate using actual selected pages, cache misses, repeats and retry allowance.

However, [ai_pricing.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/ai_pricing.py:45) still contains introductory pricing with a comment anticipating a September change. Its unknown-model behavior also returns zero. Before execution, verify and pin applicable rates; treat historical rollups as estimates rather than invoice truth.

The gateway should enforce this invariant before every billable attempt:

```text
settled cost + outstanding reservations + new request ceiling <= spend cap
```

Reserve transactionally before dispatch. Settle against returned usage afterward. Fail closed on unknown prices; retain reservations when usage or request outcome is unknown. Include all retries and all stage calls.

Anthropic’s token-counting endpoint is free, but returns an estimate; use it for forecasting, not as the sole proof of a hard ceiling. A strict reservation needs a defensible maximum input charge—falling back to the model’s request/context bound—plus maximum output charge and applicable pricing multipliers. This is conservative and can leave budget unused. [Token-counting documentation](https://platform.claude.com/docs/en/build-with-claude/token-counting).

Disable invisible SDK retries and implement the configured retry policy through the metered gateway. Start with sequential requests. A crash after the provider charges but before the response is persisted cannot guarantee exactly-once charging: retain that reservation and mark `outcome_unknown`, rather than silently repaying on resume.

Enforce a shared monthly ledger across evaluator runs and reconcile other application spending separately. A dedicated Anthropic workspace spend limit provides an additional backstop. [Anthropic spend-limit documentation](https://platform.claude.com/docs/en/api/rate-limits).

The $200 allowance does not automatically cover development, changed-pipeline reruns and the full pilot. Cache reuse reduces overlap; the full run must fit the remaining verified allowance or resume in a later budget period. Never silently switch models to finish under budget.

A concrete CLI could be:

```bash
python -m scripts.extraction_eval snapshot \
  --out /secure/fairtales-eval/pilot --entered-by pilot_import

python -m scripts.extraction_eval sample \
  --snapshot /secure/fairtales-eval/pilot \
  --books 20 --challenge-books 4 --seed 20261104

python -m scripts.extraction_eval estimate \
  --selection /secure/fairtales-eval/pilot/dev.json \
  --config baseline.json

python -m scripts.extraction_eval run \
  --selection /secure/fairtales-eval/pilot/dev.json \
  --config baseline.json --run dev-baseline \
  --max-spend-usd 20 --execute

python -m scripts.extraction_eval run --run dev-baseline --resume --execute

python -m scripts.extraction_eval score \
  --run dev-baseline --protocol protocol-v2.json --offline

python -m scripts.extraction_eval compare \
  --baseline dev-baseline --candidate dev-candidate --offline
```

Without `--execute`, `run` prints the execution/cost plan. Resuming retains the original cap and ledger. Budget exhaustion pauses scheduling and produces a clearly incomplete report.

For the headline, preregister **book-macro OCR content F1** as the primary numerical endpoint: sum page-level matched/reference/predicted token counts within each eligible book, calculate its F1, then average books equally. Also publish token-micro F1, story precision/recall/F1, metadata results by field, and character/alias F1. Every number includes its denominator, reference provenance, exclusions and completion coverage.

Call the primary figure “pilot reference-agreement” unless independent transcription provenance supports a stronger claim. A single overall pipeline percentage requires explicit research priorities or acceptance thresholds; it cannot be inferred by averaging unlike metrics.

For statistical comparisons:

- Bootstrap **books**, preserving page clusters; never treat 6,894 pages as independent observations.
- Compare variants using paired book-level differences on identical eligible references.
- Respect sampling strata and weights for population estimates from probability samples. Keep challenge results separate.
- Report effect sizes and 95% intervals, with subgroup sample sizes. Twenty development books give imprecise estimates.
- A completed 195-book run is a census description of this frozen pilot. A sampling interval does not establish accuracy on future books, nor account for incorrect references.
- Report stochastic replicate variation separately from variation between books.

The reports should include `summary.json`, `books.csv`, `pages.csv`, `calls.csv` and a local HTML inspection report. Each book view should show original/corrected images, reference/predicted text, missing/excess tokens, ordered differences, story classifications, metadata source/matches, character matching, exclusions, failures and stage costs. Failed extractions remain in intended denominators; unrun budget-stopped work is reported as incomplete, never silently dropped.

The tuning loop is then practical:

1. Review both large disagreements and a random sample of apparently successful pages, with independent transcription before showing predictions where feasible.
2. Classify problems: reference defect, image alignment, crop/rotation, OCR omission/hallucination, reading order, story semantics, metadata location/edition, character merge/split, alias attachment or parser failure.
3. Record reference corrections as reviewed local overlays. Retain original-reference and adjudicated scores.
4. Change one pipeline component and rerun only changed requests and dependent stages. A character-prompt change should reuse all OCR.
5. Compare paired development results, including regressions and cost changes; use verified-text character runs or verified-orientation OCR runs to localize causes.
6. Freeze the candidate and protocol before inspecting held-out performance.

Remaining validity limits must stay visible: imported scans may not represent live phone photography; `corrected` is not an orientation label; student transcriptions can omit text or follow different signage conventions; alias storage can lose information; model knowledge of familiar books can contribute answers; and excluded transcription gaps may disproportionately remove difficult pages. Independent review, provenance subgroups and coverage reporting mitigate these problems but do not eliminate them.

The work can land in independently reviewable commits:

| Commit | Deliverable | Required tests |
|---|---|---|
| 1 | Explicit, storage-free extraction boundaries with existing app wrappers preserved | Existing pipeline/rotation regressions; attempted Firestore/S3 writes fail; production request/output parity with fixed responses. |
| 2 | Snapshot, reference eligibility and sampling | Reference resolution, missing fields, page-number gaps, variants, duplicate images, empty-story gaps, non-story blanking, deterministic manifest replay. |
| 3 | Pure scoring and reference overlays | Reordered spread blocks, punctuation/case, repeated words, OCR failures, verified blanks, unknown truth, author incompleteness, year semantics, article stripping and ambiguous entity matches. |
| 4 | Durable cache and spend gateway | Cache invalidation, free cache hits, strict price validation, cap boundaries, metered retries, reservations, crash recovery and unknown outcomes. |
| 5 | Shared-stage runner and offline reports | Correct predicted-text character inputs; metadata/ISBN replay; dependent-stage invalidation; budget-stop propagation; scoring without credentials or network. |
| 6 | Frozen protocol and comparison tooling | Book-cluster bootstrap, paired comparisons, weighting, denominator reconciliation, incomplete-run reporting and reproducible aggregates. |

The first paid smoke run should follow those checks: one ordinary book and one known-gap book, with a small explicit cap, verifying that the resulting report exposes both extraction errors and reference defects.