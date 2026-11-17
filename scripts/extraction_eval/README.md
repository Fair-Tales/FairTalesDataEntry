# Pilot extraction validation

This CLI measures **agreement with a frozen imported pilot reference**. Its
snapshot, extraction and scoring operations use separate output directories:

1. `snapshot` reads raw Firestore documents and canonical original S3 photos.
2. `extract` reads the snapshot and writes local cached predictions. It can call
   Anthropic and, when an ISBN is found, Google Books. It cannot write Firestore
   or S3. `--dry-run` makes **no network calls**, including ISBN lookups.
3. `score` reads local references and cached predictions only. It imports no
   application runtime, creates no service clients, and needs no credentials.
4. `inventory` hashes original scans for one admitted cohort, without inference.

Run commands from the repository root with the existing virtual environment.
Keep the output directory outside this repository: images, text, responses and
reports contain research data. No command invokes git. Do not use production
credentials while testing; all harness tests use fakes and synthetic fixtures.

## Original colour scans

Use `extract --inputs scans --inputs-manifest INPUTS --snapshot SNAPSHOT --out RUN` to read the original
colour scans; `--inputs renders` is the legacy default. Requires system Poppler
(`pdfinfo`, `pdfimages`), with a clear error if missing; no Python PDF dependency.
`--scan-root` defaults to `/media/storage/Gender in Children's Literature/Books`.
Titles use the importer's normalization (lowercase, `&` → `and`, punctuation →
spaces): exactly one matching folder and exactly one PDF inside it, any basename.
When exact matching fails, `input_overrides.json` may name one existing file for
that admitted id, with a one-line reason. Overrides outside the cohort are ignored;
missing or multiple override files are errors. Each PDF must contain
one JPEG per page; embedded bytes are extracted in PDF order without re-encoding.
The cohort gate runs before any PDF read. The source choice and ordered hashes
are frozen in `run.json`; the source also separates request and stage caches.
Scan page ids are `scan-1`, `scan-2`, …; `score` uses whole-book token edit accuracy.

## Original Word references

`reference_inputs.load_reference(book_id, snapshot_dir)` resolves the admitted
id's title using the same normalization; `.doc.pdf` also matches, so duplicate
exports require an explicit override. Requires Poppler `pdfinfo` and `pdftotext`, no Python
dependency. `-raw -enc UTF-8 -eol unix` preserves content-stream order: on 140
unchanged text-layer DEV20 pages it matched 135 after whitespace normalization,
versus 131 for default/`-layout`; the remaining five differ only in word spacing.
Returned pages retain every extracted character except form-feed delimiters,
including short/empty pages; hashes cover UTF-8 and counts include whitespace.
All reads use the active cohort loader before opening files.

Explicit `published_path=` (CLI `--published-text`) permits fallback only when
no PDF matches, never on ambiguity or extraction failure. The published `Title`
column is a matching index; only the matching `Text` cell is decoded. Fallback
records its source, row and whole-book hash, with `page_count=null` (no invented
pages). Source choices are made before predictions and frozen in the inputs manifest.

## Paired inputs manifest

One command records every cohort id, scan/reference filenames, file and ordered
page hashes/counts, exact/override resolution, and `ready` or `unavailable` with
its reason. `inputs_manifest_tune.json` replaces both separate tuning inventories.

```bash
.venv/bin/python -m scripts.extraction_eval.inputs_manifest --snapshot SNAPSHOT --cohort tune --run .cache/a3c-inputs --published-text ../fair-tales-language-analysis/data/book_dataframe.json --out scripts/extraction_eval/inputs_manifest_tune.json
```

Keep `--run` outside the sealed freeze. All pairing is `book_level_only`; unequal
page counts are flagged separately, without guessing missing/split pages. Even
equal counts assert no page correspondence. Whole-book text and inserted tokens
need only ordered concatenation; alias groundedness uses predicted page text,
and failed-page/cost guardrails use scan/run records.

Scan runs embed the manifest in their frozen configuration and verify source
hashes. Unavailable books stay selected, make no requests, and count in results.
`inputs_manifest.scoring_inputs` prepares ordered whole-book reference/prediction
strings (including wordless and failed pages) and forces unavailable books to
zero. `score --snapshot SNAPSHOT --run RUN --out REPORTS` writes `summary.json`
and `book_scores.json` for the selected books, including unavailable books in
`book_denominator`. Scan reports contain this text measure and per-book character
recall, attributes and the descriptive alias figures below. `cohort_summary` in
`summary.json` and the human-readable `summary.md` report the equal-book means
and ranges described below. Overlays and comparisons are not implemented here. Stored-render runs retain their existing scoring and
reports, including forced-edge character credit, so the published 5 November
baseline figures remain reproducible.
Supply `extract --reference-root` / `--published-text` when their later scoring
paths differ from defaults or a published fallback was selected.

The preserved all-pages measure (`text_accuracy`, per-book `accuracy`) is
`max(0, 1 - token_Levenshtein / reference_tokens)` on all pages
joined in order with newlines, using the unchanged `pilot-normalization-v1`
NFKC/casefold/apostrophe and Unicode tokenizer. Empty/empty scores 1;
empty/nonempty scores 0. Failed, absent and abstained pages contribute no predicted
tokens. Every score records reference/predicted token counts, substitutions,
deletions, insertions and total edit distance. Minimum-edit ties resolve from the
start as match, substitution, deletion, insertion. The exact alignment uses
Python integer bit vectors without a new dependency or approximation.

The preserved all-pages `inserted_text_per_100` uses that same alignment:
insertions per 100 reference tokens, including predictions on reference-empty pages. With zero reference
tokens the rate is `null` (undefined), while all inserted tokens are counted.
Unavailable books have accuracy 0 and `forced_score=0`; their unknown components
and rate are `null`, not invented counts.

### Story-page headline (DECISIONS 025–026)

`story_text_accuracy` uses the same token edit formula, with **independent
story-page selection on each side**, then whole-book concatenation:

- Reference: unaltered Word-export pages whose frozen snapshot reference
  `contains_story.value` is true (observed/verified). These labels address the
  Word pages, not the scans. Require a complete, unique numbered reference page
  sequence and boolean labels; otherwise this measure is undefined. Published
  whole-book fallback text has no page labels and is also undefined here.
- Prediction: completed scan pages with `contains_story is True`, in scan order.
  The parser copies this boolean from the legacy prompt's `is_story_page`;
  legacy `page_type` strings vary and are not used. False/unknown classifications
  exclude predicted text but never remove words from the reference denominator.
- For valid region tags, keep narrative/informational text and exclude paratext
  and illustration lettering; preserve legacy untagged text. Failed/unrun pages supply no text.
  Unavailable books still score zero. No scan-to-reference page matching occurs.

Per-book `story_text` retains the edit counts. The headline and its insertion
guard, `story_inserted_text_per_100`, use this filtered comparison. The historical
all-pages accuracy and insertion rate retain their names and definitions as
companion figures; all-pages accuracy keeps its range. This prevents retained
paratext and tagged picture lettering from penalizing the story comparison.
The story flags are included in the reference fingerprint.

**Measured Iterate audit, 17 November 2026:** 55 Word exports contain 2,042 pages,
versus 2,044 scan pages (two books differ). All 55 have explicit valid recorded
story ranges and complete reference-page flags: 1,526 story pages / 38,793 tokens
and 516 non-story pages / 19,162 tokens. Contrary to the earlier review's premise,
the Word files include front/back matter: 44 contain copyright/ISBN/publication
markers, on 59 pages, all flagged non-story. Their pagination is available;
scoring remains book-level because scan correspondence is not established.

In the combined old-prompt Iterate runs, all 2,041 successful pages carry both
raw `is_story_page` and parsed `contains_story`; three pages failed. The original
all-pages minimum-edit alignment has 7,002 inserted tokens: 1,210 on predicted
story pages (17.3%) and 5,792 on other pages (82.7%). By position, the latter are
1,368 before the first predicted story page, 3,112 after the last, and 1,312 on
non-story pages between them. These are alignment insertions, not adjudicated
inventions; the positional categories do not prove page genre. No Check records
were inspected. The audit used the cohort loader and printed aggregate counts.
Reference story labels and untagged picture lettering remain measurement limits.

### Cohort means and ranges (A5e)

Scan scoring uses the `book_strata` map in `selections.json`; it never derives
strata from predictions or scoring availability. `freeze.build_selections`
produces one label for each of the 195 cohort ids, using the seal's definition:
any recorded character alias means `alias_positive`, otherwise `alias_zero`.
Proof labels must agree with the seal's existing 42/38 allocation. The map is
covered by the existing byte-for-byte `freeze verify --selections` replay and
by `load_selections`'s membership validation. The count-only reader
`freeze.alias_counts_from_manifest` can check the map without decoding titles,
text, characters or alias surfaces; the real-evidence test uses this reader.

The five primaries are story-page text accuracy, tolerant character recall, gender, human
and protagonist. Each reports its equal-book mean, contributing book and item
counts, undefined-book count and two-sided 95% range. Unknown item counts (for
example unavailable text references) stay unknown and are counted separately;
known item totals are never presented as complete. Undefined measures are
omitted; unavailable books remain with primary score zero.

Ranges use `statistics.bootstrap_books` with **20,000 shared whole-book draws**,
within frozen alias strata at their observed counts, equal weights, seed
**20261110**, sorted strata/ids and linear 2.5th/97.5th percentiles. No character,
alias, page or token rows are resampled. Each field omits undefined values within
the shared draw; draws with no defined value for that field have no estimate.
These approximate sampling ranges assume comparable independent books. A
perfect set reports `[1, 1]` and explicitly cannot quantify unseen errors.
The bootstrap's existing callers retain their original results and defaults.

Attribute agreement among found characters (`gender_found_agreement`,
`human_found_agreement`, `protagonist_found_agreement`) uses the character
recall's fixed identity matching. Only matched pairs with a coded reference
attribute enter that field's denominator; missing/invalid predicted codes disagree.
No eligible pairs means undefined. The existing best-case attribute recalls
remain unchanged: each chooses its own favourable matching over all coded
reference characters, including missed ones. `extra_characters` counts predictions
left unmatched by the identity matching, including all predictions for an empty
reference cast; it is descriptive, not a new guard. Count reports show means and
totals. `duplicate_alias_count` guards the existing duplicate-form numerator,
so extra distinct aliases cannot dilute a rise in duplicates; `duplicate_aliases`
still reports their share. Zero emitted forms means zero duplicates.

Strict character/attribute versions, alias coverage, additional forms and their
groundedness, duplicate forms, combined groundedness, inserted tokens per
hundred reference tokens and unsuccessful pages per hundred attempted pages
report pooled counts and equal-book means without ranges. Alias recall also
gets a range. These count-only summaries carry no automatic decision rule.

For proof only, each of `heldout_author_shared_ids` and
`heldout_alias_quoted_ids` is excluded separately, repeating the five primaries
and alias recall with reduced book/item counts and ranges. Each analysis uses
the remaining stratum counts, with no replacement books and no new cohort draw;
the full-set result remains primary. For tuning, rehearsal and development,
the report says these proof sensitivity lists do not apply.

Measured development demonstration on the preserved **stored renders**, using
the per-book scoring pinned by A5b–d (not evidence of scan performance):

| Primary | Equal-book mean | 95% range | Books | Items |
| --- | ---: | ---: | ---: | ---: |
| Text accuracy | Unavailable | — | 0 | Unknown |
| Tolerant character recall | 92.68% | 85.78–98.44% | 18 | 96 |
| Gender | 85.89% | 77.31–93.65% | 18 | 96 |
| Human | 67.68% | 51.98–83.01% | 18 | 96 |
| Protagonist | 87.41% | 77.50–95.73% | 18 | 96 |

The 20 books have five alias-positive and 15 zero-alias books. Two have no
recorded characters and are omitted from character/attribute means. Pooled
character/attribute counts remain **90, 81, 70, 83 / 96** (strict **82, 73, 63,
77 / 96**). Alias recall is **50.00% [10.00%, 90.00%]**, five books and ten
aliases, pooled **5/10**. Failed-page counts remain **5/772**; the equal-book
mean is **0.4694 per hundred** (pooled **0.6477 per hundred**). Text accuracy
and insertion rates are unavailable in the new whole-book Word-reference form;
no cleaned-render text proxy is substituted.

### Failure outcomes (A8a)

Evaluation failures have `status=failed` and one `failure_kind`:
`provider_error`, `output_blocked`, `refusal`, `truncated`, `invalid_response`,
`outcome_unknown`, `missing_input`, or `request_limit`. JSON and schema errors
share `invalid_response`. Cached failures retain their outcome; this does not
retry them. Unknown outcomes still stop the run and retain their charge
reservation. A provider billing rejection is a failed attempt (`provider_error`)
with `halt_reason=billing_stopped`; subsequent work remains pending. Budget stops
before dispatch retain `budget_stopped` and are not failed attempts.
Missing image files reach extraction so each affected page gets an outcome;
hash mismatches still abort input validation.

The harness checks Anthropic refusal stops, and transcription/character token
limits, through its local usage sink after accounting and before parsing. Even
valid blank-page JSON with a refusal stop is a failure. An explicit successful
wordless answer remains `completed` with empty text. Short orientation checks
retain their existing fallback behaviour. OpenAI checks every reply before
settlement, including orientation and crop checks (see A12–A13 below).

Each scan book reports `page_failures`: `failed`, `by_kind`, `attempted`,
`completed`, `unrun`, and `failed_pages_per_100 = 100 * failed / attempted`.
Attempted means completed plus failed, including successful wordless pages and
failed local input reads. Pending/budget-stopped pages are excluded from that
rate and remain visible as unrun. Zero attempts gives `null`, not zero failures
per hundred. `failed_pages` lists failed scan numbers; `unrun_pages` lists pages
without an outcome. Both contribute no predicted text, as before.

Character results retain `prediction_status` and `failure_kind`. A failed stage
supplies no characters or aliases: all recorded characters, coded attributes
and aliases remain misses. A book with no recorded characters has denominator
zero and `null` recalls, even if extraction failed; it earns no perfect-score
credit. Conditional alias coverage/grounding is also undefined without output.

The preserved 5 November dev20 run already recorded 767 completed and five
failed pages (772 attempted): three output blocks, one refusal, one truncated
transcription, or **0.647668 failed pages per hundred**. Old predictions alone
classify the latter two as parse errors; their stop reasons require the ledger.
The evidence regression test joins only failed pages' `page_extraction` calls
in memory, opening `spend.sqlite` with `mode=ro&immutable=1`. It never rewrites
evidence. Five other token-limit stops were short orientation checks on pages
whose transcriptions completed; they are not five additional failed pages.

Each scan book's `characters` result uses `score_character_recall(recorded,
predicted)` (`character-recall-v1`, DECISIONS 018). It reports strict and tolerant
numerators, the shared recorded-character denominator and both recalls; tolerant
recall is the headline. Zero recorded characters gives `null` recalls. Failed,
absent or abstained character output, and unavailable books, use an empty predicted
cast and retain all recorded characters in the denominator.

Strict edges share a complete name/alias token sequence after frozen
NFKC/casefold/apostrophe normalization and removal of only an initial `a/an/the`.
Both strict and tolerant comparisons preserve the ordered possessor signature
(words ending in `'s` or a final apostrophe, and
`my/your/his/her/its/our/their/whose`). A tolerant candidate is a proper ordered
whole-token subsequence, allowing intervening words. It adds an edge only when
each endpoint has exactly one opposite entity across **all strict and candidate
pairs in both complete casts**, deduplicating surfaces within each entity.
Ties add no tolerant edge and are reported with their candidate indices and reason.
Maximum one-to-one matching retains all strict edges, never only forced edges;
accepted tolerant edges cannot displace strict ones. `matches` marks each pair
`strict` or `tolerant`; its indices and `unmatched_reference` refer to the returned
original `reference` and `prediction` casts. A representative maximum matching
does not establish invariant ownership for explicit-alias coverage.

Each scan book also has `attributes`, from `score_character_attributes(characters)`:
separate gender, human and protagonist scores (`character-attribute-recall-v1`).
Each headline restricts the character headline's **candidate edges** to equal
valid codes, takes a maximum matching, and divides by all recorded characters
with a valid code for that field. `recall`, `numerator` and `denominator` report
that figure; `strict_recall` and `strict_numerator` use strict edges only, with
the same denominator. A missing character or absent/invalid predicted code earns
zero. An absent or unmapped recorded code leaves only that field's denominator
and is listed in `excluded_reference` and counted by reason in
`excluded_reference_counts`; it is neither a match nor a miss. An empty eligible
denominator gives `null`.

Codes follow `text_content/forms.py` and `scripts/PILOT_IMPORT.md`:

| Field | Accepted recorded codes → pipeline code |
|---|---|
| Gender | `F` → `Female`, `M` → `Male`, `NGS` → `Non-specific`; the exact four codebook labels `Female`, `Male`, `Non-specific`, `Transgender` also pass through. |
| Human | `H` → `true`, `NH` → `false` after stripping whitespace; booleans pass through. |
| Protagonist | Integer `1` → `true`, `0` → `false`; booleans pass through. The source key `is_protagonist` is used only if `protagonist` is absent. |

Recorded fields are read from the snapshot's `raw` object, with explicit top-level
fields taking precedence. Predictions must contain the exact gender labels and
actual booleans at top level; strings such as `"False"`, numeric booleans and
source abbreviations are invalid predictions. Null, missing and blank values are
absent. There is no truthiness conversion, case folding or fallback to an import
default: `NBT`, `NO`, `NGS` in human, and other unmapped codes are excluded on the
recorded side. This cannot recover an original code already overwritten by an
import fallback; a stored valid code is compared as recorded. `plural`,
`ethnicity` and `disability` are not scored: their import defaults were not human
codes. Overall gender agreement does **not** validate the absent Transgender
category.

`matched_pair_agreement` is a different, descriptive number: equal valid codes
among the fixed pairs selected by the **character** headline, divided by those
pairs with a valid recorded code. `strict_matched_pair_agreement` does the same
on its strict pairs (accepted tolerant edges are isolated, so they cannot change
the strict pairing). Both include counts, disagreements and a confusion table:
rows are recorded codes, columns are predicted codes, boolean labels are
`"False"`/`"True"`, and explicit `absent`, `unmapped` (recorded) and `invalid`
(predicted) categories retain every pair. Zero cells are included; the table
always sums to `matched_pairs`, including excluded recorded codes. Omissions,
additions and ambiguous identities stay in the accompanying `characters`
result: `unmatched_reference`, `unmatched_prediction`, `ambiguous_candidates`
(rejected tolerant candidates) and `ambiguous_matches` (selected pairs that can
be replaced in another maximum matching). All indices refer to the original casts.

**Restricting edges to equal values before matching lets each field pick its own
most favourable pairing. The three headlines need not come from one consistent
pairing.** `matches` and `strict_matches` expose each field's choices;
`pairing_changes` lists reference characters assigned a prediction different from
the character headline (including previously unmatched references). Dropping a
pair for a wrong code is not a changed pairing. These additions apply only to
scan-input scoring; stored-render scoring is unchanged. The A5e cohort summary
above reports means and ranges across books.


Each scan book's `aliases` result comes from
`alias_metrics.score_aliases(characters, pages)` (`scan-alias-figures-v1`).
All four figures have numerators, denominators and surface/owner audit entries.
They are descriptive; explicit-alias coverage is a tuning target and groundedness
is its guardrail. None gates character recall or is a headline.

- `explicit_alias_coverage`: recorded names and aliases, deduplicated within each
  owner using `normalize_character_name`, excluding the matched prediction's
  primary name. Credit requires that complete surface explicitly on that prediction;
  a fuller/shorter-name identity edge alone earns no coverage. Only owner pairs
  present in **every** maximum matching enter. `unresolved_owners` includes
  references unmatched in the selected maximum that can be matched in another;
  `unmatched_owners` lists references unmatched in every maximum. Both are outside
  this conditional coverage denominator.
- `alias_recall`: deduplicated recorded aliases recovered as explicit names or
  aliases on their matched prediction. Uses the character headline's fixed
  one-to-one owner matching, with a one-to-one intersection of normalized surfaces
  within each owner. Possessors remain significant. No recorded aliases means
  denominator 0 and recall `null`, not perfect recall.
- `additional_aliases`: distinct predicted **forms**, including primary names,
  beyond all recorded names/aliases of the selected matched owner. Unmatched
  predictions have no recorded forms. `field` distinguishes primary names from
  explicit aliases. Case and initial a/an/the are ignored with the same frozen
  character normalization; a form equal to the primary or an earlier alias is a
  duplicate, not an addition. Counts per character reconcile emitted forms,
  distinct forms, duplicates and additions. The addition share is additions over
  distinct forms; the duplicate share is repeat rows over emitted forms.
  `grounding` is grounded additions over additions. `ambiguous_forms` lists exact
  normalized forms owned by multiple predictions, including primary names; subset
  overlaps are not equality. Extras are never labelled errors. Recall and extras
  use the representative owner assignment, explicitly labelled in `owner_matching`;
  unlike coverage, their values may depend on an ambiguous assignment.
- `groundedness`: names **plus** aliases found in completed pipeline page text,
  using M1's `_grounding_ledger`, `_grounding_counts` and `grounding_support`.
  Checks are whole-token and page-local, exact first, then dropping one determiner
  (including possessives, for this existence check only). Missing/failed pages
  supply no support; missing text can therefore explain an ungrounded form.
  Editorial allowances do not count as lexical grounding. M1 deduplicates case
  within an owner's alias field, but keeps primary-name and alias fields separate;
  an alias equal to its primary is counted in both fields. This differs from the
  additional-form duplicate rule. `names` and `aliases` components expose the split.
  The guardrail always uses full stored predicted page text, independently of the
  selected text-accuracy projection.

Measured dev20 alias regression, reading only `dev20-run/predictions.json` and
`dev20-snap/manifest.json` (20 exposed books; pooled counts, **not book means**):

| Figure | Numerator / denominator | Other counts |
|---|---:|---|
| Explicit-alias coverage | 14/27 (51.85%) | 2 unresolved owners excluded |
| Alias recall | 5/10 (50%) | 5 alias-positive books; 15 undefined books |
| Additional forms | 125/212 distinct forms | 217 emitted forms; 5/217 duplicates |
| Additional-form groundedness | 106/125 (84.8%) | 19 ungrounded; 0 ambiguous shared forms |
| Names-and-aliases groundedness | 197/217 (90.78%) | 186 exact, 11 determiner-stripped, 20 ungrounded |
| Alias-only M1 component | 104/112 (92.86%) | 95 exact, 9 determiner-stripped, 8 ungrounded |
| Primary-name component | 93/105 (88.57%) | 91 exact, 2 determiner-stripped, 12 ungrounded |

Coverage reproduces the 11 November 14/27 and two excluded owners (`worm`,
`worms` in *Wiggling Worms at Work*). Recall differs from historical 6/10 by
exactly `mother` in *The Ugly Duckling*: historical `normalize_name` removed
`his` from predicted `his mother`; the ratified character normalization preserves
that possessor and earns no credit. The other five recovered aliases are
`cave dad`, `anemone`, `bristleworm`, `ugly duckling` and `the swans`.
Additional forms comprise 92 explicit aliases and 33 primary names; among the
explicit aliases alone 84/92 are grounded. Five duplicates equal their own
primary names (four in *The Three Little Pigs*, one in *Santa Is Coming to Devon*).
The historical 92.9% is **alias-only**, reproduced exactly by the M1 component;
adding primary names explains the combined figure's different denominator and rate.

The test contains per-book regression counts and skips when exposed evidence is
absent. No new aggregate estimator is introduced. M1's alias-only top level is
redundant for the scan combined guardrail once its components are reported; M2's
pairwise/subset counts and M3's subset collision flags are not needed for these
four scan figures. They and the historical audits remain intact because
`score_alias_foundation`, stored-render reports and their consumers still use them.
Scan reports are JSON written by `cli._score_scans`; `reporting.py` remains unchanged.

Measured exposed dev20 regression (20 books, 96 recorded characters; pooled
counts, **not** means across books):

| Field | Headline /96 | Strict /96 | Agreement on headline pairs /90 | Agreement on strict pairs /82 |
|---|---:|---:|---:|---:|
| Gender | 81 (84.375%) | 73 (76.042%) | 81 (90.000%) | 73 (89.024%) |
| Human | 70 (72.917%) | 63 (65.625%) | 70 (77.778%) | 63 (76.829%) |
| Protagonist | 83 (86.458%) | 77 (80.208%) | 83 (92.222%) | 77 (93.902%) |

All three historical strict agreement counts reproduce. Excluded recorded codes:
0 for every field. Changed pairings: 0 for every field, 0 characters in their
union. Character recall is 90/96 (93.750%) headline, 82/96 (85.417%) strict;
there are 6 omissions and 15 additions, 9 rejected ambiguous tolerant candidates
and 1 ambiguous selected pair (`worm` → `Worms` in *Wiggling Worms at Work*).
The omissions are `elves` and `old reindeer` (*Santa Is Coming to Devon*),
`older girl` (*The Enormous Crocodile*), `Dad` (*The Storm Whale*),
`beautiful birds` (*The Ugly Duckling*) and `worms` (*Wiggling Worms at Work*).

Dev20 headline-pair confusion tables (recorded rows → predicted columns):

| Gender | Female | Male | Non-specific | Transgender |
|---|---:|---:|---:|---:|
| Female | 18 | 0 | 2 | 0 |
| Male | 0 | 36 | 4 | 0 |
| Non-specific | 1 | 2 | 27 | 0 |
| Transgender | 0 | 0 | 0 | 0 |

| Field / recorded | False | True |
|---|---:|---:|
| Human / False | 54 | 19 |
| Human / True | 1 | 16 |
| Protagonist / False | 66 | 1 |
| Protagonist / True | 6 | 17 |

Each field's table sums to 90; all absent/unmapped/invalid rows and columns are
zero here. The regression reads only `<evidence>/dev20-run/predictions.json` and
`<evidence>/dev20-snap/manifest.json`, using `evidence_dir()`, and skips if either
is absent. It pins the counts above and reports named strict disagreements on
failure; it never rewrites evidence.

`score --text-output stored` is the default: compare all stored page text.
`--text-output analysis_without_illustration_lettering` explicitly selects
`project_analysis_text(..., include_illustration_lettering=False)`, retaining
narrative/informational regions and dropping tagged illustration lettering and
paratext. The existing projection keeps unclassified/malformed-region text intact.
The choice is recorded in the summary and every book score, together with the
normalization version; it does not require new extraction. Stored text is the
recommended convention to freeze: it exposes all added text to the guardrail
without allowing a predicted region label to hide it. This recommendation does
not ratify the choice.

For rehearsal/proof, run the same command with that explicit cohort before any
predictions, using frozen rules. Software reads only admitted files; nobody needs
to read book content. Resolve awkward filenames from existing filename/edition
metadata and record an id-specific override before predictions, without viewing
pages or text. A duplicate-text equality/hash check can run without displaying
text (as for Bottoms); do not choose between genuinely different references by
looking at predictions. If metadata cannot establish the file, keep it unavailable
and score zero, with no replacement. No fuzzy title guessing is added here.

## Cohort admissions

Every `extract` and `score` run uses `--cohort tune|rehearsal|proof` (default
`tune`). The ids-only `selections.json`, validated by `freeze.load_selections()`,
defines admission:

| Cohort | Permitted book ids |
| --- | --- |
| `tune` | 55 iterate plus 20 check; protected 40 only after a verified rehearsal lock |
| `rehearsal` | Exactly the protected 40 |
| `proof` | Exactly the held-out 80 |

`--original-experiment` has been removed. `--books` accepts exact ids, not
titles, case variants or Unicode variants. Defaults and `--limit` select only
within the named cohort. Resume requires the same cohort and lock. Legacy runs
without a cohort record cannot resume as guarded runs. `--freeze-dir` defaults
to `/home/expert/Documents/uob/fair_tales/autonomy-freeze/`; sealed exclusion
hashes and id lists are still checked, and no sealed files are rewritten.

The shared gate is `tuning.TuningLoader.load`. `JsonSources` scans JSON structure
and ids before decoding permitted book records. Excluded titles, references,
images and the global `raw_documents` duplicate are not decoded. The same gate
covers stage/prediction/ISBN caches, replay and scoring inputs. Unknown ids fail
closed. Prompt examples are a separate input: their sources must always be
iterate books, including in rehearsal and proof runs. Rendering checks the ids
before decoding example content; the application applies the same iterate-only
rule when rendering outside the harness.

To release protected40 into tuning, pass `--rehearsal-lock PATH` to a **new**
tune run. The JSON must contain `run_record` (the absolute path to the completed
rehearsal's `run.json`), its `run_id`, a timezone-aware `locked_at`,
`results_sha256` and `configuration_sha256` from the run record's `completion`.
These hash the stored JSON value bytes of the predictions and run contract,
respectively (`JsonSources.sha256`), without decoding book content. The loader
checks these against the actual completed full rehearsal record and result
file. A dry run, partial rehearsal, missing record, changed output or invented
hash cannot release the protected ids. The lock and freeze hashes remain
immutable on resume.

Tune reports expose check-book aggregates separately from iterate aggregates;
no check-book or check-page detail is written to reports. Prediction caches and
the ordinary run record retain the inputs needed for scoring and audit.

`run.json.tuning` records the cohort, start time, hashes, lock, loaded ids and refusals.
IDs are persisted before admitted reads, so an interrupted or failed reader
may conservatively count as loaded. Refusals do not invalidate proof. A saved
forbidden load records an irreversible breach and invalidated proof status;
there is no replacement or redraw. Audits run at completion/resume; before
final policy lock run:

```bash
.venv/bin/python -m scripts.extraction_eval audit-tuning --run RUN_DIRECTORY
```

Additional label/derived JSON files must declare `source_work_ids`, or have
`records` carrying `book_id`. ISBN caches require `source_work_ids`; legacy
unattributed caches are refused. The gate cannot reconstruct missing historical
provenance. It covers evaluation inputs; `snapshot` is a separate source-capture
operation, before evaluation cohorts are selected, and does not apply this gate.

The old rotation fixtures do not feed this harness. In
`scripts/rotation_prompt_eval/rot_eval.py`, `load_labels()` enumerates
`rot_labels.json`, and `load_preview()` reads `work/sample/*_preview.jpg`;
`rot_spine_probe.py` and `rot_gate_risk.py` reuse these functions.
`rot_sample.py` and `rot_fetch_manifest.py` populate those files separately.
No extraction harness module imports those scripts or enumerates that directory.
Its rotation path is `runner.local_image` → `extraction_core.extract_page` →
`image_processing.correct_page_image`, using admitted snapshot image bytes.
Those standalone rotation scripts and arbitrary file-reading scripts remain
outside this harness's cohort gate; do not use them as guarded evaluation runs.

## Holdout freeze (offline)

Reproduce the 7 November draw from a completed snapshot, or check the existing
seal read-only (including its sibling `holdout_ids.json`):

```bash
.venv/bin/python -m scripts.extraction_eval.freeze write --manifest SNAPSHOT/manifest.json --out NEW_DIRECTORY
.venv/bin/python -m scripts.extraction_eval.freeze verify --manifest SNAPSHOT/manifest.json --freeze SEALED_DIRECTORY/freeze.json
```

The fixed frame counts must match; mismatches stop without trimming. Writing
refuses existing seal/exclusion files. Verification reports differing fields
and exits nonzero on failure; only creation time and Python version are ignored.
Output contains counts and the id-set hash, never titles or book content.
Snapshot assembly remains in `snapshot.create_snapshot` (`backend=None` for an
already cached snapshot); this module neither fetches nor copies the snapshot.

Generate the versioned ids-only lists: `.venv/bin/python -m scripts.extraction_eval.freeze selections --manifest SNAPSHOT/manifest.json --freeze SEALED_DIRECTORY/freeze.json` (writes `scripts/extraction_eval/selections.json`).
Add `--selections` to the `verify` command above to compare that file byte for byte with fresh regeneration; any difference exits nonzero.
The harness can call `freeze.load_selections()` to validate counts, disjoint cohorts, membership and the held-out id hash without opening book content.

## First 20-book run

The following are commands for a human operator. Keep evidence in
`DEFAULT_FREEZE_DIR / "evidence"`, currently
`/home/expert/Documents/uob/fair_tales/autonomy-freeze/evidence/`;
`FAIRTALES_EVIDENCE_DIR` overrides this shared location for the harness and tests.
The commands below read it through the shared helper.

Paid runs refuse a ledger or output directory resolving inside the system temporary
directory (including symlinks): cleanup or reboot could erase the only paid evidence.
Dry runs may still use temporary paths.

The ledger defaults to `spend.sqlite` in the snapshot's parent directory, so a
snapshot kept directly in the evidence folder picks up the preserved ledger.

`snapshot` needs **IAM-enforced read-only** Firestore/S3 credentials;
the adapter exposes only reads, but IAM is
the independent protection. `extract` reads only the Anthropic key from its
secrets file and never constructs Firestore/S3 clients.
Versioned S3 buckets also require the read-only `s3:GetObjectVersion` permission
to fetch the captured object version.

```bash
EVIDENCE_DIR="$(.venv/bin/python -c 'from scripts.extraction_eval.evidence import evidence_dir; print(evidence_dir())')"
mkdir -p "$EVIDENCE_DIR"
.venv/bin/python -m scripts.extraction_eval snapshot \
  --out "$EVIDENCE_DIR/pilot-v1" --entered-by pilot_import \
  --secrets "$EVIDENCE_DIR/read-only-secrets.toml"

cp scripts/extraction_eval/baseline.json "$EVIDENCE_DIR/baseline.json"
```

The default snapshot makes **one capture pass** over Firestore. It does not
re-read every captured document or repeat the membership queries afterward.
The example above captures the whole `pilot_import` population; the later
`extract --limit 20` selects twenty books from that local snapshot. To capture
only particular books, supply snapshot `--books ID_OR_TITLE ...`.

Before the first Firestore read, snapshot prints a conservative read estimate
and the requested audits. An estimate above **10,000 reads** requires the
literal **`AFFIRMATIVE`**; Enter/yes does not authorize reads. The planning inputs
are `--estimated-books 195` (the entire matching population, including discovery
when `--books` selects a subset) and `--estimated-documents-per-book 50`.
`--read-confirmation-threshold` changes the confirmation threshold. Estimates
include allowances for empty queries and requested audits, but are not billing
guarantees or knowledge of the project's remaining shared quota. Set the
planning inputs conservatively for a different corpus. No Firestore count
query is used to obtain the estimate.

Concurrent source changes remain a reproducibility concern. Opt in to
`--audit-source-changes` to re-read captured documents and image metadata,
`--audit-membership` to repeat collection and image-inventory checks, or
`--audit` for both. These extra passes
can roughly triple reads for a complete snapshot and appear in the up-front
estimate. The manifest records `audits.source_changes` and `audits.membership`
separately. `source_consistent` is `null` when a clean snapshot has not completed
both audits, `true` only when both found no changes, and `false` when changes
were detected. Existing structural findings remain in `audit`; that list alone
does not mean either optional source audit ran.

Capture retains documents and query cursors together, checkpoints completed
queries, and saves partial results on exceptions, including quota exhaustion.
Resume continues from the saved position without re-reading captured documents.
Reuse the same snapshot
command, output directory and audit settings after an interruption; image
checkpoints in `images-progress.json` are reused too. Firestore
`ResourceExhausted` stops immediately without retrying and saves partial state.
The diagnostic identifies the exhausted project quota and the daily free-tier
reset at approximately midnight US/Pacific. Wait for quota availability before
rerunning the command. The estimate on resume accounts for saved work; requested
audits still perform their explicit verification reads. A completed snapshot
replay verifies local hashes without connecting to either service.

Before proceeding, inspect snapshot `audits`/`audit`/`excluded_books` and review the local
configuration. The checked-in price table was verified on 2026-11-04 against
docs.anthropic.com and this repository's `ai_pricing.py`, which is the same
table `record_api_usage` bills against; `pricing.verified` is `true`. Note that
Sonnet 5's $2/$10 per MTok is NOT stale introductory pricing - it became
permanent on 2026-08-10. Re-confirm current account input/output/cache prices,
context/output/image/request-byte limits and any applicable multipliers before
a large run, then update `pricing.models`, `pricing.source`, `pricing.verified_at`
and keep `pricing.verified` `true`. Rates are USD per
million tokens. A rate/limit cannot silently default to zero. Every extraction
config must explicitly name `settings.prompt_variant`; missing or unknown
variants are refused. `baseline.json` uses `legacy-v1`, the prompts from the
5 November baseline, and `candidate_pipeline_v2.json` uses `rules-plus-icl`.
These configs do not change the live application's prompt default. Confirm the
other settings match the intended run; they are not read from a live
admin-settings document. Generation settings follow the shared definition below;
no temperature is added.

### Stage generation settings (A9)

`pipeline_config.py` defines `<stage>_model`, `<stage>_thinking`,
`<stage>_effort` and `<stage>_max_tokens` for these stages:

| Stage prefix | Default model | Maximum output tokens |
|---|---|---:|
| `extraction` | `claude-sonnet-5` | 8192 |
| `metadata` | `claude-sonnet-5` | 20000 |
| `character_detection` | `claude-sonnet-5` | 20000 |
| `locate` | `claude-haiku-4-5` | 128 |
| `rotation` (all three probes) | `claude-sonnet-4-6` | 16 |
| `crop_quality` | `claude-haiku-4-5` | 16 |
| `theme` (application only) | `claude-sonnet-4-6` | 512 |
| `story_context` | `claude-sonnet-5` | 20000 |

These ceilings leave room for Sonnet 5's default adaptive thinking and the answer within the same token budget, with the 20,000 settings bound below the installed SDK's default-timeout non-streaming limit of approximately 21,333 tokens (128,000 × 600 / 3,600).
In the 16 November 2026 paid probe on one development book with the new prompts and `candidate_pipeline_v2.json`, joint metadata exhausted 4,096 tokens and character detection exhausted 8,192 entirely on thinking, both stopping at `max_tokens` without an answer (character recall 0), while one page used 1,697 of its 2,048 tokens under the new page schema.

Thinking and effort default to the explicit sentinel `"default"`, meaning
**omit the API option**, as before A9. This retains the exact old request body
and cache identity. The model and token cap were already sent and remain sent.
For Sonnet 5, omission means adaptive thinking and high effort according to the
bundled reference; Sonnet 4.6 and Opus 4.8 omit thinking as disabled and effort
as high. Haiku 4.5 omits thinking as disabled and has no effort parameter.
These documented defaults are expectations, not measurements of provider internals.

An override such as `"extraction_effort": "low"` sends
`"output_config": {"effort": "low"}`. `"extraction_thinking": "disabled"`
sends `"thinking": {"type": "disabled"}`. Even explicitly choosing `"high"`
or `"adaptive"` sends that choice: only the `"default"` sentinel preserves
omission. Each override changes that stage's request identity. Downstream calls
can also change if the stage produces different inputs; unchanged input bodies
still replay. Token caps include thinking tokens as well as visible output.

The shared definition validates model/option combinations, and the gateway
checks the actual body before any reservation. Supported overrides are adaptive
or disabled thinking and low/medium/high/max effort on Sonnet 4.6; Sonnet 5 and
Opus 4.8 also allow xhigh. Haiku 4.5 accepts only disabled thinking here, with
effort omitted. Manual `enabled` thinking needs a budget and is not implemented.
An evaluation model known only to the price table may use omitted options;
explicit overrides require documented support in the shared definition. This
prevents accidental invalid requests without guessing capabilities. OpenAI has
separate reasoning settings and output floors, documented below.

The local source consulted offline was bundled `claude-api` reference **2.1.322**,
at `/tmp/claude-1000/bundled-skills/2.1.322/98c07a88497fde2ff3d554084d8bd7d5/claude-api/shared/`:
`models.md` (Haiku 4.5) and `model-migration.md` (Sonnet 5, Sonnet 4.6 and
Opus 4.7/4.8 sections). Its upstream references are
[adaptive thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)
and [effort](https://platform.claude.com/docs/en/build-with-claude/effort).
No network verification was performed for A9.

The ledger already stored full provider responses, including `model`, `usage`,
`stop_reason` and request ID, beside the exact request. New reservations also
store `context.requested_settings` (model, thinking, effort, token cap and
documented default meanings), outside the cache identity. `calls.json` now
exposes `served_model` and `requested_settings` alongside its existing `model`
(requested), usage and stop reason. Historical rows replay unchanged; their
`requested_settings` is null because no contemporaneous settings record exists.
Their original request and provider response remain available. No old record is
backfilled with today's assumptions, and no database schema change is needed.

`legacy-v1` restores page, character, page-location, title-page and copyright-page
prompts. It has no joint-metadata or whole-book classification prompt: those
stages came later. The current metadata caller also changed image-label layout,
so selecting the old prompts alone cannot replay the old metadata workflow.

```bash
.venv/bin/python -m scripts.extraction_eval extract \
  --snapshot "$EVIDENCE_DIR/pilot-v1" \
  --out "$EVIDENCE_DIR/dev20" \
  --config "$EVIDENCE_DIR/baseline.json" \
  --limit 20 --seed 20261104 --max-spend-usd 16 --dry-run

.venv/bin/python -m scripts.extraction_eval extract \
  --snapshot "$EVIDENCE_DIR/pilot-v1" \
  --out "$EVIDENCE_DIR/dev20" \
  --config "$EVIDENCE_DIR/baseline.json" \
  --max-spend-usd 16 --execute

.venv/bin/python -m scripts.extraction_eval score \
  --snapshot "$EVIDENCE_DIR/pilot-v1" \
  --run "$EVIDENCE_DIR/dev20"
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
Source versions and collection membership are checked again only when their
respective optional audits are enabled. Detected source changes are flagged
and rejected by extraction/scoring: make a new snapshot after resolving them.
Unaudited snapshots remain usable, with their incomplete consistency
verification carried into the score reports.

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
  If ISBN enrichment fails, still-empty title/authors/publisher fields receive
  `lookup_failed` and leave the metadata denominators. Vision-filled fields,
  illustrators and publication year remain scored. `metadata_lookup_failures`
  reports affected books and otherwise-eligible excluded fields; each field also
  reports `lookup_failed_exclusions`. An answered `totalItems == 0` remains a
  measured absence and receives the usual missing-field score.
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
Metadata sees original ordered images without reference title-page hints; successful
ISBN provider answers (including `totalItems == 0`) are frozen locally, and values
carry their extraction sources. Failed lookups are never frozen as absence.
Oversized whole-book metadata requests fail explicitly; automatic chunking would
be a different pipeline variant.

Every billable request passes through `Gateway.messages.create`. The request
identity includes exact ordered content (including image bytes), prompts, model,
generation/API options, request version, attempt and replicate. Raw response
blocks, usage, request ID and stop reason are persisted **before parsing**.
`utilities.record_api_usage` routes to the local ledger sink during evaluation;
cache hits are never charged again. SDK retries are disabled. The default is one
attempt; `max_attempts` permits 1–5 attempts for explicit 429 and 5xx error
responses (including overload 529). Each has its own reservation and record.
Retries honour `retry-after` (seconds or an HTTP date) up to 600 seconds.
Longer waits stop the run without sleeping, report the requested wait, and
require `retry-failed` before another attempt. Otherwise retries use exponential
backoff with jitter: half to all of `min(30, 2**attempt)` seconds, starting at
attempt zero. The gateway's `wait` argument is injectable for offline tests.
Permanent parse failures reuse the cached raw response, not a paid call.

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

All runs should share `--ledger "$EVIDENCE_DIR/spend.sqlite"` (the default
for the paths above). Anthropic evaluation defaults to **$150 per billing cycle**,
leaving $50 of its stated allowance for the app; OpenAI defaults to **$200 per
cycle**. Headroom never transfers between providers. The run cap remains $16.

Paid execution needs a provider key and cycle settings; no billing attestation is
required. Anthropic uses `EVAL_ANTHROPIC_API_KEY` when available, otherwise the
app's `ANTHROPIC_API_KEY`. OpenAI requires `EVAL_OPENAI_API_KEY`. Missing or blank
keys are refused before any reservation. The run record saves only `secret_name`,
never the key or a fingerprint. Provider billing rejection still stops the run;
it does not trigger a switch to another key.

Both supplied configurations set `start_day: 1` for each provider, with comments
marking these as placeholders until Chris confirms each subscription's renewal
day. Caps therefore use calendar-month bookkeeping meanwhile. Other configurations
must also supply the selected provider's cycle setting.

Cycles begin at 00:00 UTC on that day and end just before the next start.
Days 29, 30 and 31 clamp to the last day of a shorter month, then return to the
configured day: day 31 means January 31 → February 28 (29 in a leap year) →
March 31. Admission checks the current time on every reservation, including a
run spanning renewal. Settlement stays in the cycle where the request was
reserved. Old-cycle reservations remain recorded against that cycle; they do
not consume the next cycle's allowance.

Use `--cycle-cap-usd AMOUNT` to lower the selected provider's **total cycle cap**
to the actual remaining credit after app/other usage. This is not additional
headroom. The ledger tracks evaluation spending, not the provider's credit
balance. The option cannot exceed the provider maximum.
Changing the maximum requires editing that provider's `cap_usd` in
`billing_cycles`; an already recorded cycle cap can only decrease. A lower cap
persists for that cycle across other runs, even if they omit the option. A new
cycle starts with the configured maximum unless the option lowers it again.
The recorded start day is fixed to prevent accidentally moving existing spend
into a different cycle. The former `--monthly-cap-usd` option is removed.

All attempts count: successful calls settle to their measured charge; failed
and retried calls retain their reservation until explicitly settled, even after
an HTTP rejection. Unknown outcomes stay reserved. Confirmed zero-cost attempts
can be explicitly settled to zero during reconciliation; error status alone
does not release headroom. An over-cap reservation is refused before dispatch,
with the cap, reservation, used amount and headroom in the error.

Read the current cycle's cap, settled spend, outstanding reservations and
headroom for both providers without credentials, network access or writes:

```bash
.venv/bin/python -m scripts.extraction_eval headroom \
  --ledger "$EVIDENCE_DIR/spend.sqlite"
```

The command uses the cycle settings recorded in the ledger; an unconfigured
provider is reported as unavailable, never assigned an assumed calendar cycle.
Opening an old ledger with `Ledger` adds a `calls.provider` column and the
`billing_providers`/`billing_cycles` tables. Existing timestamps and nano-dollar
charges are preserved; the provider tag is extracted inside SQLite without
loading book content. The old `month` column and `months` table remain for
compatibility but no longer govern admission. `headroom` never migrates files.
Test preserved evidence on a copy under `.cache/`; do not migrate the original.

Three replicates or a new pipeline are additional potential spend, not included
in the $16 subset plan. These caps do not authorize spending beyond the actual
allowance, buying credits or switching providers to use another balance.

Local artifacts are `manifest.json`, content-addressed images, `selection.json`,
`run.json` (source/dependency/config hashes), `eval.sqlite` (stage state), the
shared `spend.sqlite` (requests/responses/ledger), `predictions.json`, lookups,
corrected images and `calls.json`. Writes are atomic or transactional; concurrent
runners for one directory are rejected. Version/config/code changes require a
new run directory. Identical request identities reuse the shared cache across
runs; parser/stage changes can reinterpret responses without paying again.
Metric/reference changes require only offline rescoring. A dry-run freezes its
config too: if you change prices afterward, use a new run directory.

After a rate/overload retry allowance is exhausted, or after resolving a provider
billing stop, explicitly authorize another attempt without editing SQL or
changing `--replicate`:

```bash
.venv/bin/python -m scripts.extraction_eval retry-failed --run "$RUN_DIR"
```

This offline command uses the ledger recorded in `run.json`. It records each
authorization and its UTC time in `retry_events`, for requests owned by this run
whose last attempt is retryable or a billing stop. Repeating the command before
resuming has no further effect. It never sends requests, changes paid responses,
or releases old reservations. All old attempts still count against their caps;
new attempts require fresh headroom. Completed responses replay free, including
responses obtained after an earlier recovery. Permanent failures remain cached.

Resume extraction with the same settings, ledger and replicate. Authorized
failed stages can run again; completed stages with unchanged inputs stay cached.
Recovered pages refresh dependent classification, metadata reconciliation and
character stages. Identical provider requests still replay free; changed inputs
are new requests and need fresh budget. A billing stop
(including `credit_balance_exhausted` and `enforced_spend_limit_reached`) halts
immediately and remains stopped until this explicit authorization. Timeouts,
interrupted dispatches and unknown charges are listed by request key for manual
reconciliation and are never authorized for another send. Old 5xx attempts
already recorded as unknown remain unknown. If source hashes changed, use a new
`--out` as described below, after running `retry-failed` on the original run.

For ISBN-cache recovery, a legacy `lookups/*.json` entry with both `value` and
`raw_response` null is a failed request, not an empty provider result. Updated
runs automatically ignore that entry and replace it only after a provider answer;
resuming a completed metadata stage retries ISBN enrichment without repeating
vision. A dry-run does not retry or modify the entry. Offline scoring also reads
these legacy entries to exclude affected fields from existing predictions without
changing the run. Do not remove those files before rescoring: they preserve the
evidence that the missing fields reflect a request failure.

When recovering a run created with older source hashes, use a **new `--out`** and
the **same shared `--ledger`, configuration, snapshot, selection and replicate**.
The code fingerprint intentionally prevents resuming old code in place; the new
run starts with a clean ISBN cache and reuses identical paid request responses
from the shared ledger. Do not edit the frozen run contract or clear the paid
response cache. This clears the poisoned ISBN results from the recovery run
while preserving the original run, including any evaluation still in progress.

### OpenAI storage

Every OpenAI Responses and Chat Completions payload includes `store: false`, and
attempts to enable storage or background execution are refused before reservation.
This avoids stored-response state, not all retention: abuse-monitoring logs can
retain content for up to 30 days.

### OpenAI reasoning, outcomes and prices (A12a, A12b, A13)

Official OpenAI documentation checked **2026-11-16**; no API calls were made.
The supported model IDs and standard USD prices per million tokens are:

| Model/source | Allowed reasoning efforts | Input | Cache read | Write 30m | Write 12h | Output |
|---|---|---:|---:|---:|---:|---:|
| [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) | low, medium, high, xhigh, max | 10 | 1 | 12.5 | 25 | 50 |
| [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) | low, medium, high, xhigh, max | 2 | .1 | 2.5 | 5 | 10 |
| [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) | none, low, medium, high, xhigh, max | 1 | .05 | 1.25 | 2.5 | 5 |
| [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | none, low, medium, high, xhigh, max | .1 | .01 | .125 | .25 | .5 |

The design's “Sol” means `gpt-6.1-sol`. The earlier
[GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) documents
`none` support, but is not in this adapter's supported registry. Unknown model
capabilities are refused before reservation.

Every stage in `pipeline_config.py` has `<stage>_openai_reasoning_effort`
(default `low`) and `<stage>_openai_output_floor` (default `25000`, configurable
from 256 to 128000). The effective cap is the larger of its ordinary
`max_tokens` and this floor. The shared helpers forward both; the gateway records
the effort, floor, effective cap and cache lifetime in the request identity.
Changing even a floor below the effective cap misses the local cache. Anthropic
request bodies retain their previous defaults.
Pinned `max_output_tokens` limits must accommodate the floor; a smaller limit
refuses the request before reservation rather than silently raising the limit.

The [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
recommends an initial allowance of at least 25,000 tokens. This is a starting
budget, **not evidence of successful extraction or a guarantee of visible output**;
no finite floor prevents reasoning from exhausting it. A smaller tuned floor
needs evidence. Reasoning appears in `output_tokens_details.reasoning_tokens`
(Chat: `completion_tokens_details.reasoning_tokens`), already within the billed
output total. We never add it again.

The [Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create)
specifies `reasoning.effort`, `max_output_tokens`, refusal content blocks, and
`status=incomplete` with `incomplete_details.reason=max_output_tokens` or
`content_filter`. The
[Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
uses `reasoning_effort`, `max_completion_tokens`, `message.refusal`, and
`finish_reason=length` or `content_filter`. These map to A8a's `refusal`,
`truncated`, and `output_blocked`. Empty/whitespace or malformed output maps to
`invalid_response`; a failed/cancelled response maps to `provider_error`.
Known usage is charged and stored atomically as `failed`, retaining the native
response and request ID. Replay raises the same kind; old completed-but-invalid
records are also refused. Explicit wordless-page JSON remains a success.

The [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
prices writes at 1.25 times input for `30m`, or 2.5 times input for `12h`.
These replace ordinary input pricing. Ordinary input is total input minus reads
minus writes. Responses supplies `input_tokens_details.cache_write_tokens` and
`cache_write_tokens_details.{ttl_30m_tokens,ttl_12h_tokens}`; the split must sum
to writes. Chat supplies `prompt_tokens_details.cache_write_tokens` and supports
only `30m` at request level. Both endpoints default here to explicit
`prompt_cache_options: {"ttl": "30m"}`. Each pinned price row must contain
`cache_ttl: "30m"` and the model's write tariff above; the reservation includes
the highest input/read/write rate. Responses can explicitly request `12h` only
with a matching pinned row. Mixed lifetimes and per-block overrides are refused.

The Responses reference labels the lifetime split optional, whereas the caching
guide says both counts are returned. We fail closed on its absence, even with
zero writes. Chat's write count is optional in the schema; we require it
explicitly. Missing/null/invalid counts, inconsistent splits, unexpected lifetime
usage or usage beyond 272K input leave the reservation outstanding and stop
further OpenAI dispatch in that run, including through a restarted gateway.
Missing write tariffs stop before dispatch. Long-context surcharges and regional
or nonstandard service tiers are outside this adapter's priced scope.

Offline fixtures in `tests/fixtures/provider_gateway/openai.json` are synthetic
recorded-format shapes, not live captures. Tests cover both endpoint payloads,
every stage/model effort mapping, rotation, floor/lifetime cache misses, charged
failures and replay, explicit wordless output, all four models' four tariffs,
reasoning counted once, and unknown usage stopping subsequent requests.

## Review, adjudication and comparisons

`score` writes `summary.json`, `book_scores.json`, `books.csv`, `pages.csv`,
`calls.csv`, the frozen protocol and an offline `inspection.html`. Open the HTML
locally to inspect original/corrected images, reference/predicted text, missing
and extra tokens, ordered differences, story labels, metadata, character
attachments, errors and costs. It uses no remote scripts, fonts or images.
The console and HTML prominently state which snapshot audits ran and whether
source consistency was verified. `summary.json` includes `snapshot_audits`,
`snapshot_audit_status` and `snapshot_source_consistent`. Missing legacy audit
provenance is reported as unknown, never assumed to mean audited.

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
`--compare-run "$EVIDENCE_DIR/candidate"` to the baseline `score` command.
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

### Field-aware pipeline comparison (v3, offline)

`score` now defaults to `protocol_v3.json`; `protocol.json` and the frozen baseline
configuration remain historical artifacts. `--compare-run` emits `raw_to_raw`,
`corrected_to_corrected` (when an overlay is supplied), and a separately labelled
`fixed_baseline_measurement_repair`. Always use a fresh `--out` directory when
scoring a frozen run. Both systems receive the same reference overlay and
bibliography adapter. The registry includes recorded-alias/name-variant/character
recall, M1–M4 diagnostics, separate evidenced merge/split rates, OCR, story,
credits, scoped publication fields, regression guards and operational coverage.
Reference alias precision/F1 are never gates. Missing evidence stays explicitly
undefined, including attribution/rotation fixtures that have not been supplied.

Each endpoint carries per-book counts and sampling metadata. Ratios are recomputed
inside 10,000 paired book/group draws (seed 20261105). Probability samples also
report a linearized ratio interval with finite-population correction; non-census
singleton strata cannot estimate that variance. Descriptive intervals concern the
selected development books only. Point gates never convert undefined evidence to
passes, and no endpoint compensates for another. M4 fixes the baseline occurrence
universe and refuses a fixed-input claim after character inputs change.

The versioned C4 scorer requires scoped, evidenced publication targets. Legacy
publisher/year values are preserved but supply no v2 gold denominator. Literal
imprint agreement, scoped reviewed equivalence, publishing-house agreement,
year/scope/territory and joint events remain separate. The original metadata
lookup-failure exclusions remain historical diagnostics; comparison uses additive
operational known-fact scores that retain failed lookups in denominators.

The checked-in `evidence/dev20-v1/adjudications.json` contains only the seven
supplied text-recovery verdicts (80 tokens) and Chris's blank p6 verdict plus the
explicit p24 image-hash transfer. The Santa correction retains the full cached
28-token text including `Everybody, PULL!`; the source table's shorter excerpt is
recorded as a discrepancy. `empty_reference_cohort.json` freezes 101 monitoring
pages, with two verified blank page records from one independent image. Other
hash-identical records are not silently adjudicated. A new overlay validates the
snapshot, original field/provenance hash, image hash, value type and convention;
legacy unchecked overlays cannot be used for candidate comparison.

`exposure.py` freezes all 34 exposed IDs, including both Gruffalo books and the
incidental `a_letter_to_santa` exposure. Group-linked books remain quarantined.
`select_holdout_books` constructs the 27 alias-positive / 13 alias-zero sampling
plan with seed 20261105, recomputed eligibility counts, weights, reference hashes
and exposure-after-unblinding records. It performs no service access or inference.

Dev20 explicitly recorded alias recall is **6/10 under equivalence normalisation,
across five books**, while strict raw-surface credit is **4/10**. Ten aliases make
this a smoke test, not evidence of improvement; the legacy 88/106 name-variant
measure includes canonical names and has a different meaning.

### Tuning noise and attempts (A5f, offline)

Scan `score` also writes `tuning_scores.json` for the tuning cohort: numeric
per-book measures/counts, ids for machine pairing, frozen cohort and reference
identities, configuration/code identity, replicate and run ids, and ledger cost
per scored book. No titles, aliases, text or failure details enter that file.
Treat Check's per-book records as machine inputs; read only the aggregate
command output and attempt log. An absent ledger gives unknown cost (`null`),
not zero. Ledger cost is the run's settled spend plus separately reported
outstanding reservations; cache reuse can make a run cheaper than a fresh pass.

Calibrate one unchanged configuration on all 75 books: **R=20** runs for
cheap character stages, **R=5** for transcription, including the initial run.
Use purpose-named `extract --replicate` tokens, e.g. `cal-char-01`,
`screen-<idea>-1`, `gate-<idea>-base-1`, `gate-<idea>-cand-1`. Never reuse screen
tokens at a gate: a new output directory does not make a cached response fresh.
The numeric tools can check supplied arms and calibration, not an unprovided
screen history. `tuning-noise` saves its replicate tokens; `tuning-attempt`
refuses collisions across arms or with calibration, even with different run ids.
Old noise files without tokens require offline regeneration. The comparison uses separate fresh
runs of both configurations: **r=4 per arm on Iterate, r=8 on Check** for cheap
stages; **r=1** on both sets for transcription.

Use `--replicate-stages character_detection` for character calibration, or
`--replicate-stages page_extraction` for transcription. Supply the same option
on **every run, including replicate 1**, and use a new `--out` for each replicate.
For replicates other than `1`, only listed request labels get that replicate's
cache identity; everything else uses replicate `1` in the shared `--ledger`.
An uncached upstream request still needs to run: populate the initial run first.
Omitting the option retains the original behaviour and cache keys (all requests
use the given replicate).

The option accepts space-separated `calls.json` labels: `page_extraction`,
`character_detection`, `joint_metadata`, `story_context`, `locate_key_pages`,
or the configured `rotation_model` / `crop_quality_model` IDs. The default small
image-check labels are `claude-sonnet-4-6` and `claude-haiku-4-5`; if both checks
use the same model, that label refreshes both. Changed inputs naturally change
downstream request keys. In particular, changed predicted story text after
transcription refreshes characters; identical character inputs replay their
cached response. Include `character_detection` too if it must refresh even when
transcription is identical.

For a character-only candidate, set `settings.character_prompt_variant` while
keeping `settings.prompt_variant` unchanged. Missing/null means follow
`prompt_variant`; the override is shared by foreground app calls, background
jobs and the harness. It changes no page-extraction request bytes/cache keys.
Keep cached text and other settings fixed; a dry run should show no pending page
calls. This setting adds no admin widget.

For example, with `SNAPSHOT`, `FREEZE_DIR`, `TUNING_INPUTS` (the frozen 75-book
scan manifest), `CONFIG` and `LEDGER` pointing to your existing local artifacts,
preview the second run of each calibration:

```bash
.venv/bin/python -m scripts.extraction_eval extract \
  --snapshot "$SNAPSHOT" --freeze-dir "$FREEZE_DIR" --cohort tune \
  --inputs scans --inputs-manifest "$TUNING_INPUTS" --config "$CONFIG" \
  --ledger "$LEDGER" --replicate cal-char-02 --replicate-stages character_detection \
  --out runs/calibration-2 --dry-run

.venv/bin/python -m scripts.extraction_eval extract \
  --snapshot "$SNAPSHOT" --freeze-dir "$FREEZE_DIR" --cohort tune \
  --inputs scans --inputs-manifest "$TUNING_INPUTS" --config "$CONFIG" \
  --ledger "$LEDGER" --replicate cal-page-02 --replicate-stages page_extraction \
  --out runs/transcription-calibration-2 --dry-run
```

Replace `--dry-run` with `--execute` for metered execution under the existing
caps and confirmation. Use tokens `cal-char-01` through `cal-char-20`, or `cal-page-01` through
`cal-page-05`, with distinct output directories, then score every run. Keep the
configuration, selection and shared ledger fixed. The estimate printed **after
cache replay** excludes completed upstream pages; character-only misses retain
just the historical per-book allowance (`2 * $0.0103` per pending book, about
$1.55 for 75), not the initial full-run estimate. This is a planning allowance,
not a measured character price.

`run.json` and `tuning_scores.json` record the sorted `replicate_stages` set;
`null` (or absent in old scores) means all stages. `tuning-noise` and
`tuning-attempt` reject different sets, including mismatches with the noise
floors or separate Check runs. Use the same set for calibration and both arms.

Scoring identity includes a hash of the current source fingerprint (reusing
`runner.code_fingerprint`, including all scorer modules and shared normalization /
projection code). Any scorer edit changes `measurement_id`; this deliberately
also invalidates it for other pipeline source edits. Rescore every compared run
into a new report directory and regenerate noise floors together. Mixing scorer
identities is refused; paid responses need not be regenerated.

These offline commands do no extraction, source reading, network access or spending:

```bash
.venv/bin/python -m scripts.extraction_eval tuning-noise \
  --runs runs/calibration-{1..20}/reports/tuning_scores.json \
  --out runs/noise.json

.venv/bin/python -m scripts.extraction_eval tuning-attempt \
  --baseline runs/baseline-{1..4}/reports/tuning_scores.json \
  --baseline-check runs/baseline-{1..8}/reports/tuning_scores.json \
  --candidate runs/candidate-{1..4}/reports/tuning_scores.json \
  --candidate-check runs/candidate-{1..8}/reports/tuning_scores.json \
  --floors runs/noise.json --target character_recall \
  --hypothesis 'Recover missed characters without losing other agreement' \
  --change 'Describe the one rule or example changed, and any stated cost gain' \
  --log runs/attempts.jsonl
```

The example uses Bash brace expansion. Runs 1–4 cover both sets; 5–8 need only
Check. Each run must cover every frozen book in the set for which it is used.
The optional `--baseline-check` and `--candidate-check` lists default to the
main arm lists. For transcription, supply five calibration paths and one fresh
path per arm, omitting the Check overrides. Actual counts are recorded, not
inferred from the target. Each arm uses one configuration, equal run counts
between arms per set, and distinct runs/replicates; neither arm may reuse a
calibration run or a run from the other arm. The Python `verdict()` and
`append_attempt()` accept a single scored run, a list for both sets, or
`{'iterate': [runs...], 'check': [runs...]}` for each arm.

Name the target in the experiment plan **before** running the candidate; the
command records that declaration but cannot verify when it was made. Targets
are the five primaries, `alias_coverage`, `groundedness`,
`story_inserted_text_per_100`, `unsuccessful_pages_per_100` or
`duplicate_alias_count`. The all-pages text figures and found-character agreement
are descriptive, not targets. Fractions use 0–1 units;
insertions and failed pages use counts per hundred, consistently with scoring.

The floor `f` is the **sample SD of R replicate equal-book means**, separately
for every measure and set. It is neither the between-book SD nor a standard
error. Calibration requires the same configuration, measurement, references
and complete book panels. Recalibrate after model/settings changes. Each arm
averages within books first, then equally across books. Shared undefined books
are omitted and counted only when eligibility is unchanged across calibration
and both arms. Changed eligibility or a missing required floor gives no verdict.
Fewer than two calibration runs cannot yield a verdict. A measured zero SD is
valid, but unchanged set means alone do not establish caching.

Under DECISIONS 020, difference noise is **d = f × sqrt(2/r)**. Keep only when:

- Iterate's target improvement is **strictly greater than t(.975, R−1) × d**.
- No affected Iterate primary/guardrail or Check primary/target falls by more
  than **t(1−.05/m, R−1) × d**, using its own measure/set noise.

Here `m` counts downside clauses, excluding the redundant Iterate target
clause. A measure is omitted only if its per-book values are identical in every
calibration and comparison run on both sets; the floor file retains value
fingerprints and the verdict names omitted measures. With cached text accuracy,
story insertions and failed pages, and a varying duplicate count, `m=9` for
character recall and `m=11` for aliases. Identical duplicate counts are omitted
by the same rule; report-only measures never add clauses.
An unchanged cached target cannot establish improvement. The Student-t
quantiles use a standard-library cosine-integral recurrence and bisection;
tests independently integrate the density at df=4/19 and p=.975/.99375.

Groundedness and primaries improve upwards; insertions, duplicates and failed pages improve
downwards. Check guardrails other than the target remain report-only. Counts
alone cannot accept a change. Cost is reported, not gated: explain any
increase's intended gain in the hypothesis/change. Arm spend sums distinct
runs once, even when a run supplies both sets; per-book cost is the mean of
the reported per-run rates, and unknown costs remain null. Counts are means
across runs within books, summed across books.

The command prints aggregate clauses and appends one JSON object to the shared
log: counts, primary/guardrail/coverage means, costs, configuration and run ids,
floor provenance, **R, r per set, m**, omitted measures, thresholds, verdict and
cumulative attempts/acceptances. Check output remains aggregate-only. Rejected
and unscorable attempts remain and count towards 30. After acceptance, use
fresh runs of that candidate configuration as the next baseline; otherwise
retain the incumbent configuration. The log checks this configuration chain
and refuses to append once 15 changes are accepted or 30 attempts are logged.
Keep one log for the whole tuning process. It uses an append and a directory
lock, not a rewritten history.

Stop earlier when no reachable error category remains. Before freeze, make the
one fresh final-version versus fresh original-baseline confirmation in §5.10;
these offline commands do not schedule it or claim it has happened. The pure
`verdict()` function can compare fresh full-cohort scores without appending a
further tuning attempt. Preserve that separate confirmation result.

`comparison.py` is **unused by this tuning path**, including its registry,
`compare_metrics()` bootstrap/ratio comparison and `point_gate_passed`
zero-margin gates. Nothing was deleted: stored-render `score --compare-run`
still calls `compare_metrics()`, and stored-render reporting still calls
`comparison_protocol()` and `book_comparison_record()`.

#### Synthetic verification

The tuning-loop test reproduces the design's SD .05 row with 1,500 trials and
seed 161126, drawing independent normal set-mean errors equivalent to per-book
SD .05 over 55/20 books. It redraws 20 calibration runs and 4/8 fresh runs per
arm each trial. No book content is used. The expected keep rates are 2.8%,
49.5%, 93.6%, 96.1% and 0.0% for no change, +1/+2/+3-point target gains, and a
+3-point gain with a −3-point gender loss respectively (both sets). Tests allow
the design's ±2.6 percentage-point Monte Carlo margin and also check an
unchanged Check target and Check-only gender harm.

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_extraction_eval_tuning_loop.py
```
