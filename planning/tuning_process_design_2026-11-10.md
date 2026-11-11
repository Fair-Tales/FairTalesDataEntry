# Tuning process design — 10 November 2026

**Status: design for Chris's approval; revised 11 November. No inference run or spend.** Implements DECISIONS 017–018 and the [analysis plan](analysis_plan_2026-11-10.md). Unresolved judgements are in the [independent review](tuning_design_review_2026-11-11.md).

## 1. What the baseline's errors actually are

Exposed dev20: 20 books, 18 character-positive, 96 recorded characters, 82 strict matches. Inputs were typeset renders without pictures; text errors were not analysed.

| Field | Errors | Kind | Count | Record → pipeline | Reached by |
|---|---|---|---:|---|---|
| Characters | 14 missed of 96 | Qualitative fuller/shorter counterparts; only eight pass the amended rule | 10 | cat → The Old Cat; Dad → Noi's dad | aliases, scoring |
| | | Record splits what the pipeline groups | 3 | worm, worms → Worms | convention, examples |
| | | Genuinely omitted | 1 | elves | |
| Human | 19 wrong of 82 | Non-human called human; 16 are animals with ordinary names, in three books | 18 | Marlon, Granny, Billy | **pictures** |
| Gender | 9 wrong of 82 | Explicit pronoun/possessive cue missed | 3 | hare, hyena, mammoth | prompt, examples |
| | | Gendered noun, no pronoun | 2 | cow, hen (record Female) | DECISIONS 018 |
| | | Pipeline assigns what the record leaves open | 3 | Santa-nav → Female | prompt |
| | | Unclear from the text | 1 | | |
| Protagonist | 5 wrong of 82 | Ensemble cast; record marks several | 4 | one book | convention, examples |
| Pages | 5 failed of 772 | Blocked 3, refused 1, truncated 1 | 5 | | fallback |

- **Naming accounts for most misses:** strict 82/96 (85.42% pooled; 85.44% equal-book), tolerant 90/96 (93.75%; 92.68%). The old 95.8% wrongly credited `Dad` (drops a possessor) and `old reindeer` (two candidate owners). All eight added matches lack the reference variant as an alias; broader explicit-alias coverage is 14/27 (two unresolved reference owners excluded).
- **Human/non-human is beyond prompts and examples:** the character stage never sees a picture.
- **All nine gender errors concern non-human characters.**

These diagnoses concern renders; their frequency on scans is unmeasured.

## 2. What "better" means

Report equal-book primaries and paired changes from the last accepted version. Characters uses tolerant recall, strict recall and explicit-alias coverage; coverage is a tuning target. Guardrails:

- **Alias groundedness** (names found in page text; 92.9% on dev20) must not fall.
- **Inserted text** (added tokens per 100 reference tokens, including wordless pages) must not rise.
- **Failed pages** per 100 (0.65 on dev20) must not rise.
- **Cost per book** ($0.75 on dev20) may rise only for a stated gain.

## 3. Which books do what

| Set | Books | Use | Projected 2SE half-width (points) |
|---|---|---|---|
| Iterate | 55: 34 exposed + 21 unused | Read failures; source examples | 2.70 |
| Check | 20 unused, ten per alias stratum | Scores only | 4.47 |
| Rehearsal | 40 protected | Once after freeze; then Iterate has 95 | 2.05 after joining |
| Proof | 80 held out | Once | ±0.7–±8, single-arm projections from analysis plan |

- **Assumed**, unmeasured paired SD = 10 points. `2×SD/√n` gives half-widths, not detectable gains; approximate 80%-power gains are 3.8/6.3/2.9 points for 55/20/95 books. Measure paired spread at the first comparison.
- **Check draw:** the first ten ids per stratum by SHA-256 of `tuning-check|20261110|<stratum>|<id>`, saved in the freeze folder before the baseline.
- **Leave one book out:** a book that supplied an example is scored with its own examples removed. `load_example_pack(held_out_book_ids=…)` exists but is not wired in; ten dev20 books supply all 26 examples today.

## 4. Run-to-run noise

The cached Anthropic reference says Sonnet 5 rejects non-default sampling settings. Pin supported model/thinking/effort/token settings per stage in the request identity; record served model, usage and stop reason. Recheck settings before execution; OpenAI uses §7's provider-specific settings.

Rerun with `--replicate`: characters twice more on 55 Iterate books ($1.15), transcription twice more on ten ($9.19). The sample SD of three panel means estimates a preliminary noise floor; it measures neither Check noise nor full-pipeline noise across 55 books.

## 5. The loop

1. **Baseline from scans.** Run the old prompts and the new on 30 Iterate books; carry the better to the other 25 and the Check set. The `main` merge is waiting on this comparison.
2. **Noise floor** (§4) and **model screen** (§7).
3. **Error analysis.** Read every Iterate miss, classify and count. Tally gender cues beyond pronouns by kind and bring each kind to Chris before adding it to a prompt. Never infer gender from illustrations or stereotypes (DECISIONS 018).
4. **One hypothesis, one change:** a rule, an example or a structural step, aimed at the largest category a lever can reach.
5. **Re-score the whole Iterate set**, not just the cases the change was written for.
6. **Accept only if** the category's count falls; the targeted primary rises by more than twice its floor, or the count falls by a third; no other primary falls by more than its floor; no guardrail worsens. Otherwise revert.
7. **Log one row per attempt:** hypothesis, change, counts before and after, primaries, guardrails, cost, verdict. Reverted attempts stay.
8. **Consult the Check set three times only:** after the baseline, when Iterate stops improving or after five accepted changes, and before freeze. Read its five figures and nothing else. If Iterate has gained and Check has not, beyond Check's own noise, remove recent changes newest first.
9. **Stop** when no category has five cases a lever could reach, after 15 accepted changes, or when the cycle's budget is spent.

Work characters first, at about a cent/book; batch text changes ($25/re-pass).

## 6. The two levers, and what neither reaches

**Examples.** Fixed curated pack, without retrieval. Tag each example with category and log row; at most two/category and twelve/stage, using minimal excerpts. Before adding examples, move text to the existing private S3 bucket and local evidence folder; only IDs, tags and SHA-256 enter git.

**Prompt development.** An edge-case fix is steps 4 to 6; the full re-score is the regression test. Each accepted fix adds its cases to a must-stay-fixed list checked on every later run.

**Neither lever reaches:**

- *Human/non-human.* One small picture call per book returning only human/non-human, or unresolved, for named characters. Screen OpenAI for this call in cycle 1 (§7); 3 cents/book is an unmeasured planning allowance, not a price. Illustrations supply no other character field.
- *Blocked and refused pages.* A recorded failure status and one fallback attempt. Today a blocked page is stored as blank.
- *The name gap.* The amended scoring rule; recording the missing aliases remains tuning work.

## 7. Model choice: both providers from cycle 1

Screen on 12 Iterate books with baseline prompts; reuse identical incumbent requests. Anthropic candidates from the 3 November reference: Sonnet 5/5.5 and Haiku 5.5 for transcription; Sonnet 5/5.5 and Opus 5.5 for characters; Sonnet 4.6 versus Haiku 5.5 for rotation; low versus default transcription effort. Recheck Anthropic availability/settings/prices before execution. Rotation fixtures must pass the same ID filter: review §4.8 records five held-out and two protected books there.

Add **GPT-6 Luna** for transcription, characters, rotation and the human/non-human picture call, with **GPT-6.1 Sol** as the quality comparison for transcription/characters. Both accept image and text inputs; start Luna at `reasoning.effort=none` for tiny calls and Sol at `low` (Sol does not support `none`). [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Standard short-context input/output/cached-read/30-minute-write prices per million tokens are **$0.10/$0.50/$0.01/$0.125** for Luna and **$2/$10/$0.10/$2.50** for Sol, checked 11 November. These are token tariffs, not scan-book forecasts. [Pricing](https://developers.openai.com/api/docs/pricing).

Cap the screen at $15/provider. Keep the incumbent unless better beyond noise, or equal and current/cheaper. OpenAI independently reads Iterate pages/casts without other outputs/references, supplies further candidate evaluations and, if selected, the picture call. This protects Anthropic's app balance. Model changes require a new baseline; OpenAI outputs cannot substitute for Anthropic runs. Freeze any second-reader role before proof; re-screen only on retirement.

## 8. Cost

Ledger recount: **$15.045192/20 = $0.7522596/book**: transcription $0.459514, rotation $0.232912, metadata $0.047207, characters $0.010411, helpers $0.002216. At those historical rates: 75 books $56.42; one 55-book character pass $0.57 ($2.22 with the assumed picture call); one 55-book transcription pass $25.27; rehearsal40 $30.09; proof80 $60.18. Baseline's 105 book-passes cost $78.99 without reuse, $70.52 if the second 30-book arm reuses rotation/metadata.

**Proposed allocations, not measured costs.** Separate billing cycles and balances; Anthropic evaluation cap $150 leaves $50 of its stated $200 for the app. OpenAI cap $200 uses the second stated allowance. Reduce both to actual remaining credit after other usage; count failures, retries and reservations, never transfer headroom across providers or buy credits. Check scan costs on two Iterate books first.

| Cycle | Anthropic (maximum $150) | OpenAI (maximum $200) |
|---|---|---|
| 1 | Baseline $79 + noise $11 + screen $15 + twelve character iterations $7 = $112; $38 for Check, retries and scan-cost growth | Screen $15 + pictures $10 + independent Iterate reads/candidate evaluations $150 + retries $25 = $200 |
| 2 | Text pass $26 + rehearsal $31 + proof $61 = $118; $32 for Check/retries or reopened tuning | Pictures $10 + selected rehearsal/proof stages and Iterate evaluations $175 + retries $15 = $200 |

OpenAI volumes depend on measured costs and named comparisons. Rehearsal/proof uses the frozen provider per stage once; allocations are alternatives, not duplicate proofs. Defer work that cannot fit. The linked Pro $200 tier supplies $200/cycle, prorated initially, without rollover; **Codex through API keys is excluded**. Linkage, balance and account limits remain unverified. [Credit terms](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

## 9. Prerequisites

References are to the [review](fable_review_2026-11-10.md). Complete offline checks, including 11–13, before billing probes; all must pass before book calls.

| # | What | Accepted when |
|---|---|---|
| 1 | Anthropic evaluation workspace with $150 limit; both providers' credit linkage (3.10; Chris) | Bounded probes settle against intended allowances; local admission reserves charges inside §8 caps |
| 2 | Ledger and evidence on a durable path (1.3) | A run resumes from cache after a reboot |
| 3 | Scan inputs, references from the Word exports, page mapping frozen on exposed books | All 55 Iterate books load; unmapped ones are listed |
| 4 | Prompt variant explicit; old prompts restored as a variant (3.1, 2.1) | Unchanged config replays dev20 from cache; no variant is refused |
| 5 | Amended primaries, ranges, alias coverage and guardrails (3.3, 3.4, 3.8) | Self-reference scores 1; tied names/possessors follow the plan; fabricated text moves inserted text |
| 6 | Frozen selections; ID guard before content decoding in every mode (3.2, 3.6) | Tuning refuses held-out IDs and protected IDs until rehearsal outputs are locked, including fixtures/examples/caches; Check supplies no examples/errors; explicit rehearsal/proof permits only its frozen cohort |
| 7 | Leave-one-book-out wired into rendering (3.5) | Run record shows no example from the book scored |
| 8 | Failures recorded and recoverable (3.11, 5.1) | A simulated rate limit costs one call; a blocked page counts as failed |
| 9 | Generation settings explicit, in the request identity (§4) | Changing effort misses the cache |
| 10 | One configuration for harness and production (3.13) | Identical request bytes on a fixture page |
| 11 | OpenAI storage control (3.12), before any page request | Captured Responses/Chat payloads explicitly contain `store: false` |
| 12 | OpenAI reasoning and stage token floors (3.12) | Effort reaches payload; settings/floor change cache identity; rotation returns a valid label; empty/refused/truncated replies remain charged failures, not successes (explicit wordless output is valid) |
| 13 | OpenAI cache-write pricing (3.12) | Price fixtures match input/read/write/output tariffs; unknown usage stops dispatch. Current code marks writes unpriced, not zero; pin supported cache lifetime in identity and price it before calls |

Use foreground Responses with `store:false`: it avoids default stored-response state (otherwise at least 30 days), **not all retention**. Abuse logs normally retain content up to 30 days; prompt-cache state may remain up to 24 hours. [Data controls](https://developers.openai.com/api/docs/guides/your-data). Pin `prompt_cache_options.ttl="30m"`; its write tariff replaces ordinary input pricing for those tokens, not an extra charge. [Caching](https://developers.openai.com/api/docs/guides/prompt-caching).

Adjudication is unnecessary; review §2 app defects still gate the `main` merge. Checks above are future work, not passed checks.

## 10. Hand-off

Freeze: commit and tag the prompts, the example pack's hash, models and settings, scoring code, page mapping and the change log. Run the frozen pipeline and analysis once on the protected 40.

- **The rehearsal may change** anything mechanical that stops the analysis running as written: inputs, mapping, scoring bugs, failure handling. Each is fixed, re-tagged and noted.
- **If its figures fall short of the Check set's** beyond what the ranges explain, tuning reopens with the 40 added to Iterate. Check is already an adaptive tuning diagnostic; it is no longer a clean independent signal.
- **It may not change** the five primaries, the interval, the references or the held-out ids.

Then the proof on the 80, once, every attempt listed.

## What this cuts

Automatic prompt optimisers, example retrieval, cross-validation folds, model judges, a separate regression framework and the 57 pilot books. Alias recovery and first-cycle OpenAI use are retained under Chris's rulings.

## Sources

[Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing) · [Plans and pricing, Legacy section](https://claude.com/pricing) · [Release notes](https://platform.claude.com/docs/en/release-notes/overview) · [Adaptive thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking) · [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)

## Open questions for Chris

None added. DECISIONS 018 settles gender cues, pictures and names; the independent review argues the remaining technical choices before approval.
