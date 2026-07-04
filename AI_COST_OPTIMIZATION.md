# AI pipeline cost-optimization — analysis & decisions (2026-07-04)

Record of the experiments run to reduce Claude API cost across the two pipelines
(the one-time **pilot import** and the ongoing **live app**) **without sacrificing
data quality**, and the decisions they justify. Raw experiment artefacts (scripts,
per-page results, prompts, caches) are under the session scratchpad
`.../scratchpad/ocr_eval/` and `.../scratchpad/idea2/`.

Verified model facts (checked via `models.list` + live calls on this account):
`claude-opus-4-8`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-haiku-4-5` are
all real GA ids. Pricing used: Sonnet 5 **$2/$10** per MTok (intro; standard
$3/$15 from ~Sep 1 2026), Sonnet 4.6 $3/$15, Opus 4.8 $5/$25, Haiku 4.5 $1/$5.
Image tokens ≈ ⌈w/28⌉×⌈h/28⌉.

## Cost baseline (before changes)
- **Live app** (student photographs a book): ≈ **$0.50/book** (~24 pages), dominated
  by per-page Sonnet-5 OCR at 2576px (~70%). Metadata/locate/character/QC calls are the rest.
- **Import** (~200 books from PDFs): ≈ **$52–64** one-time. Buckets: image-only-page
  OCR on **Opus 4.8** (~1,049 pages, ~$22); clean+judge QA on Sonnet 4.6 (~5,136
  pages, ~$20); illustrator/publisher web lookups (~$10).

Confirmed the dominant cost is **per-page vision OCR**, so the optimizations target
OCR volume and OCR unit-cost.

## Experiment 1 — OCR quality: cheaper model (import) & lower resolution (app)
**Method:** ground truth = the PDF text layer of pages that have a *trustworthy*
layer (≥25 letters, ≤2 garbage chars, ≥90% normal chars). Render those pages as
images and OCR them (as if there were no text layer), score **recall** (fraction of
real words captured — the data-quality metric) and WER. Deliberately targeted
**difficult books** via a difficulty score: genuine OCR-garbage chars
(`| \ / _ = + ~ ^` etc., excluding legit smart-quotes/ellipses), mojibake "glue"
tokens, page word-density, and low text-layer coverage. 6 hard books × 8 densest
pages = **48 pages** × 5 conditions = **240 OCR runs**. 4 pages were
ground-truth-limited (all conditions identically low → bad text layer) and excluded
from the headline; clean set n=44.

**Results (clean set, recall):**

| Condition | mean recall | min recall | pages <0.9 |
|---|---|---|---|
| Opus 4.8 @2576 (production ref) | 0.9918 | 0.875 | 2 |
| Sonnet 5 @2576 | 0.9912 | 0.844 | 2 |
| Sonnet 5 @2000 | 0.9895 | 0.844 | 2 |
| Sonnet 5 @1568 | 0.9908 | 0.844 | 2 |
| Opus 4.8 @1568 | 0.9923 | 0.875 | 2 |

**Verdict 1 — switch import OCR Opus 4.8 → Sonnet 5.** Paired per-page, 44/48 pages
tie (|Δrecall|≤0.001); largest single-page gap 0.031 (~1 word of 32); every hard
(400–500-word) page tied. Quality equal within noise. Cost/book of OCR:
Opus $0.835 → Sonnet **$0.332 (−60%)**.

**Verdict 2 — app resolution 1568px holds quality.** Sonnet-5 recall flat across
resolution (2576=0.9912, 1568=0.9908). The code comment claiming "1568 measurably
hurt OCR" **did not reproduce** with the current model, even on the densest pages
(506/503/447-word pages all 1.00 at 1568). Measured $/book (near-square real
renders, Sonnet 5 intro): 2576 $0.332 → 1568 **$0.243 (−27%)**; the token-cap basis
gives up to −55%. **2000px** saves little on these near-square pages (~$0.33) but
adds headroom; **1568px** is the real lever and is data-supported.
Caveat: ground truth came from pages that *have* a text layer; the genuinely
image-only, most-stylised pages the app faces are slightly harder than this
sample's ceiling — hence 2000px conservative default, 1568px available.

Eval API cost: **$4.69** (cached; re-runs free).

## Experiment 2 — neighbour-continuity OCR-skip (import)
**Idea:** for an image-only page, read the *neighbouring* pages' text (free from the
PDF text layer) and judge whether the story flows continuously across the gap; if it
does, the page is genuinely wordless → **skip OCR**. Only OCR when text looks
missing. A cheap **text-only** judge replaces an expensive vision OCR call.

**Opportunity:** of ~1,049 image-only story pages, **764 (73%)** are flanked on both
sides by text-layer pages (actionable); 285 are in consecutive-blank runs/edges
(keep OCR'ing — no neighbour text).

**Validation (ground truth = one OCR per page, n=68 flanked pages, 20 books):**

| Outcome | Count |
|---|---|
| Correctly skipped genuine blanks (OCR saved) | 54 |
| Correctly flagged real-text pages (OCR'd, no loss) | 3 |
| **False skips (silent data loss)** | **1** |
| Harmless wasted OCRs | 10 |

81% of OCR calls avoided; flanked image-only pages are ~94% genuinely wordless. The
single false skip lost **zero unique words** (its text was a verbatim duplicate
already in the neighbour). **Verdict: IMPLEMENT-WITH-GUARDS.**

**Guards:** skip only when `flows AND not-missing`; **fail-safe → OCR on any judge
error** (never skip on failure); tag skipped pages `text_source="skipped_wordless"`
(queryable); Pass-2 `fits_context` flag + per-book containment cross-check are two
backstops. Savings overlap the model-swap (both cut the OCR bucket): against Opus
the skip alone saves ~$11.5; combined with Sonnet-5 the OCR bucket is
**$22 → ~$5.4**.

## Decisions
1. **Import OCR → Sonnet 5** (`--ocr-model claude-sonnet-5`). −60% OCR, no quality loss.
2. **Import neighbour-skip** — implemented via a **reusable, Streamlit-free
   continuity-check module** so the same check also serves the live app as a
   validator QA flag ("story doesn't flow across these pages → human review").
3. **App extraction resolution** default **2000px**, and made **admin-configurable**
   (1568px available for cost, higher for quality) rather than hardcoded.
4. **Admin settings panel** — a global Firestore-backed config exposing the
   cost/quality API parameters (resolution, per-flow models, max_tokens, feature
   toggles), editable by admins **without a code deploy**, gated behind an explicit
   safety toggle + warning (defaulted off) so parameters can't be changed by accident.

## Cost impact
- **Import:** ~$52 → **~$35** (Sonnet-5 OCR + neighbour-skip; clean+judge and lookups now dominate and are kept as the quality layer).
- **App:** ~$0.50/book → **~$0.36/book at 1568px** (the at-scale lever); 2000px default is the safe starting point, tunable per batch from the admin panel.

## Caveats / non-goals
- Batch API (−50%) not adopted: for a one-time import the refactor isn't worth ~$10–15; not applicable to the interactive app.
- Prompt caching ≈ $0 here (prompt prefixes are below the ~1,024-token cache minimum; images precede text).
- The resolution eval's ground truth is text-layer pages; genuinely image-only stylised pages are marginally harder — hence the conservative default + admin control + human-validator backstop.
