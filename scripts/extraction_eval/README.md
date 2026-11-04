# Pilot extraction validation

This CLI measures **agreement with a frozen imported pilot reference**. It has
three separate operations, each resumable using the same output directory:

1. `snapshot` reads raw Firestore documents and canonical original S3 photos.
2. `extract` reads the snapshot and writes local cached predictions. It can call
   Anthropic and, when an ISBN is found, Google Books. It cannot write Firestore
   or S3. `--dry-run` makes **no network calls**, including ISBN lookups.
3. `score` reads local references and cached predictions only. It imports no
   application runtime, creates no service clients, and needs no credentials.

Run commands from the repository root with the existing virtual environment.
Keep the output directory outside this repository: images, text, responses and
reports contain research data. No command invokes git. Do not use production
credentials while testing; all harness tests use fakes and synthetic fixtures.

## First 20-book run

The following are commands for a human operator. Replace `/secure/fairtales-eval`
with your protected research-data directory. `snapshot` needs **IAM-enforced
read-only** Firestore/S3 credentials; the adapter exposes only reads, but IAM is
the independent protection. `extract` reads only the Anthropic key from its
secrets file and never constructs Firestore/S3 clients.
Versioned S3 buckets also require the read-only `s3:GetObjectVersion` permission
to fetch the captured object version.

```bash
mkdir -p /secure/fairtales-eval
.venv/bin/python -m scripts.extraction_eval snapshot \
  --out /secure/fairtales-eval/pilot-v1 --entered-by pilot_import \
  --secrets /secure/fairtales-eval/read-only-secrets.toml

cp scripts/extraction_eval/baseline.json /secure/fairtales-eval/baseline.json
```

Before proceeding, inspect snapshot `audit`/`excluded_books` and review the local
configuration. The checked-in price table is deliberately **unverified**, because
the repository's Sonnet 5 introductory pricing is stale. Confirm current account
input/output/cache prices, context/output/image/request-byte limits and any
applicable multipliers, then update `pricing.models`, `pricing.source`,
`pricing.verified_at` and set `pricing.verified` to `true`. Rates are USD per
million tokens. A rate/limit cannot silently default to zero. Confirm settings
match the intended baseline; the template freezes repository defaults, not a
live admin-settings document. Generation temperatures are left as production
currently sends them.

```bash
.venv/bin/python -m scripts.extraction_eval extract \
  --snapshot /secure/fairtales-eval/pilot-v1 \
  --out /secure/fairtales-eval/dev20 \
  --config /secure/fairtales-eval/baseline.json \
  --limit 20 --seed 20261104 --max-spend-usd 16 --dry-run

.venv/bin/python -m scripts.extraction_eval extract \
  --snapshot /secure/fairtales-eval/pilot-v1 \
  --out /secure/fairtales-eval/dev20 \
  --config /secure/fairtales-eval/baseline.json \
  --max-spend-usd 16 --execute

.venv/bin/python -m scripts.extraction_eval score \
  --snapshot /secure/fairtales-eval/pilot-v1 \
  --run /secure/fairtales-eval/dev20
```

Execution prints the selected book/page counts, historical estimate, outstanding
work and caps, then requires the literal **`AFFIRMATIVE`** before constructing a
paid client. Enter/yes does not authorize calls. Repeating the same extract
command resumes completed stages and cached requests. Omitting `--execute`
defaults to dry-run. A dry-run verifies image hashes, performs local processing
and replays available responses, stopping each uncached stage before dispatch;
its incomplete predictions go to `dry_run_predictions.json`. Unknown responses
cannot be simulated, so downstream stages wait for their required inputs.

The estimate is `2 * (0.0111 * pages + 0.0103 * books)`: **$15.95 for 20 books /
700 photos**, or about **$16.11 for 707 photos**. It recalculates from the actual
selection and terminal stages, with exact response-cache hits free. Conditional
rotation/crop requests cannot all be known until earlier responses arrive. This
is a planning estimate with a historical retry allowance, not current billing
truth or a promise of completion within $16. The cap stays $16; conservative
per-call reservations can pause work below that amount. Scoring still reports
intended denominators and incomplete work. Do not silently increase the budget.

For an explicit subset use `--books "BOOK_ID_1" "BOOK_ID_2" ...`; `--limit`
counts **whole books**, never the first N pages. Default sampling is seeded and
stratified by photo-count quantile and observed text density (with an explicit
simple-random fallback when there are too few slots to cover all strata).
`selection.json` freezes the full candidate inventory, selected IDs, roles,
strata and inclusion probabilities. Empty text alone never means picture-only.

To implement the design's 16 random + 4 challenge allocation, add
`--challenge-books "ID_1" "ID_2" "ID_3" "ID_4"` to the first selection command.
A human must identify these four from the snapshot: the earlier gap rationale
was superseded, and this repository design supplies no usable named IDs. Review
rotation/crop problems, wordless narrative, dense text, spreads, decorative text
and rich character sets. Challenge results remain separate from probability
estimates. Review duplicate/near-identical editions before treating unselected
books as held out; exact duplicate-image groups are recorded in the selection.
`holdout_group`, `held_out_book_ids` and `held_out_exclusions` keep unselected
members of a selected duplicate group out of held-out claims without enlarging
the paid subset. The execution command above omits selection flags and reuses
the frozen selection, including any challenge books.
Do not inspect held-out predictions while tuning.

## Reference eligibility and provenance

Snapshotting sets each field's status **before any prediction exists**:

| Status | Meaning | Scoring |
| --- | --- | --- |
| `observed` | Usable imported value, not independently verified | Reference agreement |
| `verified` | Independently reviewed annotation in a versioned overlay | Separate verified-reference result |
| `missing` | Absent, dangling reference, unknown value or unusable year | Excluded with a count |
| `ambiguous` | Conflicting alignment/annotation | Excluded pending review |
| `not_annotated` | No annotation supplied, including theme defaults | Never treated as a negative label |

`validated_by='pilot_import'` does **not** make a field verified. Raw fields,
document references, update times and available import diagnostics remain in the
snapshot, with absent fields absent. Author names resolve from forename/surname;
publisher/illustrator names use `name` with legacy fallback. Document IDs are
not compared as names. Unknown names, dangling entities and `published=-1` do
not become reference truth. Credit lists are conservatively marked incomplete.

The measured empty-story-page finding overrides the original design. An
observed empty `contains_story=True` reference stays eligible with
`kind='wordless'`. A successful empty prediction is `agreed_wordless`; a
successful nonempty prediction is `disputed_wordless`. Neither contributes
token F1. Reports show intended/completed wordless denominators, agreement rate,
coverage, end-to-end agreement, failed/unrun pages and extra-token counts. Failed
parsing or missing output cannot count as agreement. Only adjudication can turn
a disputed page into a confirmed hallucination or a corrected reference.

Empty non-story text is `not_annotated`: the importer deliberately blanked it,
while the current prompt can transcribe title/copyright matter. Missing text is
still missing; it is not automatically wordless. Story-label eligibility is
independent from OCR eligibility.

Five pilot metadata-only books have no photographs and are excluded by name:
**I Love Christmas; Invisible Isabelle; Seasons; Ten Wriggly, Wiggly
Caterpillars; Things You Should Know about Bugs**. Other books retain usable
pages even when some annotations or images are absent.

The snapshot uses original `page_N.jpg` objects, never `_cropped` or `_display`
derivatives. It preserves real page numbers, image hashes, available S3 versions,
referenced entities, source timestamps, gaps, duplicates and ID/folder conflicts.
Source versions and collection membership are checked again at completion.
Changed-source snapshots are flagged and rejected by extraction/scoring: make a
new snapshot after resolving the changes. An interrupted snapshot resumes its
frozen inventory; a completed snapshot replay verifies local hashes without
connecting to either service.

## What the scores mean

* **OCR:** normalize Unicode (NFKC), case, whitespace and apostrophes. Tokens
  ignore punctuation except internal apostrophes; retain numbers, diacritics,
  repetitions and stop words. Per photograph, matched words are the multiset
  overlap. Precision penalizes extra words, recall penalizes omissions, and F1
  is `2*matched/(reference+predicted)`. Sum counts within each book, compute its
  F1, then average books equally for the primary **book-macro F1**. Token-micro
  F1 is secondary. Reversed spread blocks can preserve word content while
  changing meaning/order; ordered CER and WER expose this and may exceed 100%.
  No adjacent-page pooling occurs. `[?]`, `[illegible]`, `[unreadable]` and
  `[unclear]` consume unmatched tokens even when both sides contain the marker.
* **Story:** story is positive. A 2×3 table retains true/false/failure, with
  unrun counts separately visible. Conventional precision/recall/F1/accuracy
  use valid outputs; coverage, end-to-end recall and end-to-end accuracy retain
  failed and unrun intended pages. Imported broad all-story defaults and
  wordless-story prompt ambiguity limit an accuracy interpretation.
* **Metadata:** strict normalized exact agreement by field; title/publisher
  also retain continuous edit similarity. Reviewed equivalences are separate,
  not automatic fuzzy credit. Complete credit sets use set agreement;
  incomplete author/illustrator lists measure recovery of known credits without
  penalizing potentially correct extra names. Surname-only agreement is not
  accepted. Years match integers exactly and report differences; edition year
  versus first-publication year requires review, not a ±1 allowance.
* **Characters:** book-local one-to-one matching using primary names and
  attached aliases, with reviewed equivalences. Strip one leading
  `the/a/an/my/your/his/her/its/our/their` symmetrically, but preserve `Tom's mum`.
  Normalization collisions and ambiguous matches remain visible with bounds.
  Duplicate predictions cannot both match one reference entity. Name/alias
  credit follows entity attachment and does not depend on which variant was
  canonical. Empty imported lists do not establish absence; import collisions
  and annotation completeness qualify precision. Visual-only labels, when
  independently annotated, are reported separately. The detector consumes only
  **predicted** story flags and OCR, so this is an end-to-end character run.
* **Aggregation:** intended denominators, exclusions, failures and incomplete
  stages accompany every endpoint. Failed or unrun eligible text recovers zero
  words; the incomplete flag prevents this being mistaken for a finished run.
  Wordless results and challenge books have explicit subgroups. Book-cluster,
  stratified bootstrap intervals preserve sampling weights; paired comparison
  requires identical reference hashes and sampling metadata. Twenty development
  books give imprecise estimates. A full-pilot result describes this frozen
  census, not future-book performance. Unlike metrics are never averaged into
  one unsupported overall “accuracy” percentage.

## Durability, cost and pipeline boundaries

`ai_runtime.py` injects frozen settings, client and local usage sink into shared
application helpers. `extraction_core.py` reuses production EXIF/crop/two-step
rotation, `vision_json` OCR, photo-first metadata location and character
extraction. Existing app entry points keep their behavior. The evaluator uses
the named `eval-v1` parser: it rejects missing/nonboolean `has_text` or story
flags and malformed text rather than crediting malformed JSON as wordless.
Metadata sees original ordered images without reference title-page hints; ISBN
lookup results are frozen locally, and values carry their extraction sources.
Oversized whole-book metadata requests fail explicitly; automatic chunking would
be a different pipeline variant.

Every billable request passes through `Gateway.messages.create`. The request
identity includes exact ordered content (including image bytes), prompts, model,
generation/API options, request version, attempt and replicate. Raw response
blocks, usage, request ID and stop reason are persisted **before parsing**.
`utilities.record_api_usage` routes to the local ledger sink during evaluation;
cache hits are never charged again. SDK retries are disabled. The default is one
attempt; configured retries apply only to explicit 429 rejections and each is
metered. Permanent parse failures reuse the cached raw response, not a paid call.

SQLite transactions enforce:

```text
settled cost + outstanding reservations + new request ceiling <= cap
```

The reservation uses the pinned full model input/context bound and output
ceiling, including the largest pinned input/cache rate, rather than treating a
token estimate as a hard limit. Missing usage, timeouts, interrupted dispatches
and unknown outcomes retain their reservation and stop scheduling. A human must
reconcile these against provider records before reusing that request; the tool
does not guess or automatically repay it. Known charges above a configured
ceiling are retained and flagged. No model is silently substituted.

All runs should share `--ledger /secure/fairtales-eval/spend.sqlite` (the default
for the paths above). The shared monthly cap defaults to $200 and the run cap to
$16; both are frozen, not authorization to spend beyond the human's allowance.
Reconcile application/other-workspace spending separately and configure an
account-level spend limit as an additional backstop. Three replicates or a new
pipeline are additional potential spend, not included in the $16 subset plan.

Local artifacts are `manifest.json`, content-addressed images, `selection.json`,
`run.json` (source/dependency/config hashes), `eval.sqlite` (stage state), the
shared `spend.sqlite` (requests/responses/ledger), `predictions.json`, lookups,
corrected images and `calls.json`. Writes are atomic or transactional; concurrent
runners for one directory are rejected. Version/config/code changes require a
new run directory. Identical request identities reuse the shared cache across
runs; parser/stage changes can reinterpret responses without paying again.
Metric/reference changes require only offline rescoring. A dry-run freezes its
config too: if you change prices afterward, use a new run directory.

## Review, adjudication and comparisons

`score` writes `summary.json`, `book_scores.json`, `books.csv`, `pages.csv`,
`calls.csv`, the frozen protocol and an offline `inspection.html`. Open the HTML
locally to inspect original/corrected images, reference/predicted text, missing
and extra tokens, ordered differences, story labels, metadata, character
attachments, errors and costs. It uses no remote scripts, fonts or images.

Review disagreements **and a random sample of apparent successes**. Where
possible transcribe independently before looking at model predictions. A local
overlay is explicit and versioned; for example:

```json
{
  "version": "review-1",
  "snapshot_id": "COPY_FROM_MANIFEST",
  "reviewed": true,
  "changes": [
    {"book_id": "BOOK_ID", "page_id": "PAGE_ID", "field": "text",
     "value": "Independently transcribed text", "status": "verified",
     "provenance": "reviewer/date/independent transcription"}
  ]
}
```

Pass `score --adjudications /path/review.json`; original-reference reports stay
at the output root and reviewed reports go under `adjudicated/`. Eligibility is
frozen from the reviewed overlay **before** predictions are opened. Name/imprint
equivalences require a document such as
`{"version":"names-1","reviewed":true,"equivalences":{"publisher":{"old imprint":"reviewed canonical name"}}}`
and `--equivalences /path/names.json`. Strict matching remains separately visible.

For a paired experiment use a new extract output/config, then add
`--compare-run /secure/fairtales-eval/candidate` to the baseline `score` command.
The comparison reports candidate-minus-baseline effects with book-cluster
intervals; references and book selection must agree. For stochastic diagnostics,
use three separate runs with `--replicate 1`, `2`, `3` on the **same four explicit
development books**. Keep each result and inspect variation/classification flips;
never choose the best replicate. Freeze the pipeline/protocol before held-out
extraction. Independently verified-text character diagnostics require a clearly
named separate variant; the baseline never substitutes reference text.

## Limits on an accuracy claim

The importer obtained page text from PDF text layers or Claude OCR and optional
AI cleaning; correlated errors can make agreement look better than accuracy.
Title/author/year generally originate in a spreadsheet, but some years use AI
fallback. Illustrator/publisher came from AI lookup. Characters/aliases came
from SQLite but can lose entries through name/alias collisions. Human-validated
whole-book text was an import cross-check, not a page-aligned transcription
source. Themes were never annotated. Independent reviewed references support a
separate accuracy interpretation only for their eligible fields and units.

Agreement on wordless pages is useful evidence, not proof of independent human
verification. Imported scans may differ from live phone photos, `corrected` is
not an orientation label, signage/transcription conventions vary, visual-only
characters are outside a text-only detector, and model familiarity with a book
can contribute answers. Sampling intervals cannot correct reference errors.

## Offline verification

```bash
.venv/bin/python -m pytest -q
```

Harness fixtures exercise reference eligibility, normalization/denominators,
one-to-one aliases, snapshot resume/integrity, no-network scoring, raw response
replay, costs/reservations, interruption/unknown outcomes and subset execution.
The four existing login E2E tests require a separately running Streamlit server;
they fail in the serverless environment and are not weakened or skipped here.
In the implementation sandbox Chromium itself could not launch (`Operation not
permitted`), so the actual full-suite result was 652 passed and 27 E2E setup
errors, before any connection to Streamlit.
