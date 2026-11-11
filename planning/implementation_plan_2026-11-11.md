# Implementation order — 11 November 2026

**Planned checks, none passed.** T/A/R = 10 November tuning design/analysis plan/Fable review; D = DECISIONS. `E/` = `scripts/extraction_eval/`; `P/` = `text_content/`; stems mean `.py`. New filenames are proposals. **I implement; orchestrating session verifies and commits each item; C marks Chris-only actions.** S/M/L indicate relative effort; — means independent.

**Gate H:** no branches until the `claude-dev` rewrite finishes; use recorded prompt hashes, not obsolete commit ids. Preserve evidence immediately. Producers enter git; evidence/book text/examples stay outside this public repository in `../autonomy-freeze/evidence/`. Held-out80/protected40 stay closed: ids/counts only, synthetic fixtures for tests.

## A. Before any paid call — probes excepted only as specified below

`A<n>` references T§9 prerequisite n. Add code checks to existing `tests/test_*.py`; run `pytest -q <changed test files>` offline, expecting the stated outcomes. External checks use the stated procedure.

| ID | Change and files | Runnable acceptance → expected result | Depends; size |
|---|---|---|---|
| A1a | **C:** Anthropic workspace/$150 limit, both credit linkages; instructions: `E/README.md`. | Inspect settings → intended workspace/allowances, cycle dates/balances recorded privately; settlement below. | —; S |
| A1b | Provider/cycle admission in `E/{billing,store,gateway,cli}`. | Reserve across cycle/provider boundaries → independent caps; failures/retries/outstanding reservations count; excess refused. | —; M |
| A2a | Preserve scratchpad ledger/snapshots/runs/census now; after H, durable defaults in `E/{cli,store}` (R1.3). | Hashes/row counts agree; reboot then cache-only replay → identical results, zero dispatches. | —; S |
| A2b | Recover **three** freeze scripts into `E/`; invocations: `E/README.md`. | Selection replay reproduces ids; synthetic producers reproduce expected artefacts; preserved hashes agree. | A2a; S |
| A6a | Frozen selections in `E/{sampling,tuning}` and ids-only `E/freeze.json`. | Replay T§3 hash draw → Iterate55, Check20 (10/stratum); original80/40 unchanged; author/alias-exposure sensitivity ids retained (A). | A2b; S |
| A6b | Pre-decode guards in `E/{tuning,json_sources,cli,runner}`. | Every mode/cache/example/rotation fixture refuses excluded ids before decoding; tuning refuses protected ids until rehearsal lock; Check figures only; rehearsal/proof exact cohorts. | A6a; M |
| A3a | Colour-scan loader: `E/scan_inputs` (new), `E/snapshot`. | Exposed fixture loads in order; inventory accounts for all55 inputs. | A6b; M |
| A3b | Uncleaned Word extraction: `E/reference_inputs` (new). | Short/empty pages survive; published-text fallback only where Word cannot map (A Inputs). | A6b; M |
| A3c | Split/merged page mapping: `E/scan_inputs`; external manifest. | Exposed-book rules → all55 scans load in order; unmapped references listed; unavailable cases remain zero, no replacements. | A3a,b; L |
| A4 | Restore explicit baseline variant: `P/{pipeline_prompts,ai_prompts}`, `E/{baseline.json,cli}` (R3.1). | Missing variant refused; baseline hashes match; dev20 dry replay near-total hits, misses explained. | A2a,A6b; M |
| A5a | Whole-book text primary/insertions in `E/scoring` (A Text). | Self=1; empty/nonempty=0; failed/unavailable stays a miss; fabricated wordless-page text increases inserted tokens. | —; S |
| A5b | Maximum cast matching: `E/{normalization,scoring}` (A Characters; D018.3). | Unique subsequence matches; changed possessor needs shared alias; tied candidates add nothing; duplicate-name self-reference=1. | —; M |
| A5c | Gender/human/protagonist graphs in `E/scoring` (A primaries; D018.7). | Same matching as the Characters headline, equal-field, over all reference characters → missing field=0; a tolerant-matched character has its codes compared; strict version reported beside; confusion counts reconcile. | A5b; S |
| A5d | Alias outputs: `E/{alias_metrics,reporting}` (A aliases; D018.3). | Explicit-only coverage/invariant owners; unresolved counts; one-to-one recall; empty denominator absent; deduplicated extras' grounding share/owner ambiguity reported, never errors. | A5b; M |
| A5e | Five means/ranges/sensitivities in `E/{statistics,reporting}`, `P/evaluation` (A ranges/reporting). | Equal-book, shared stratified 20,000 draws, seed20261110, sorted ids, linear percentiles reproduce; perfect→[1,1] with unseen-error caveat; exclusions reduce denominators; no KL/pass-fail. | A5a,c,d,A6a; M |
| A8a | Evaluator failure outcomes in `E/{runner,store,scoring}` (R3.11,5.1). | Blocked/refused/timed-out page → failed, never successful blank; counted in failed-pages denominator. | —; S |
| A5f | Noise/guardrails/attempt verdict in `E/{comparison,statistics,reporting}` (T§§2,4,5.6–7). | Target gain >2×Iterate floor with unchanged Check accepts; beyond-floor regression rejects; reverts logged; failures/cost/grounding/insertions reported. | A5e,A8a; M |
| A7a | External examples; ids/tags/hashes: `P/pipeline_examples.json`; loader: `P/pipeline_prompts` (T§6; D017.5). | Private pack hash matches; public pack has no excerpts; excluded sources refused. | A2a,A6b; S |
| A7b | Leave-one-book-out: `P/pipeline_prompts`, `E/runner`, `extraction_core`. | Recorded render excludes target-book examples, retains others. | A4,A7a; S |
| A8b | Backoff/explicit retry-failed in `E/{gateway,store,cli}`. | Simulated rate limit makes one initial dispatch; retry waits `retry-after`; billing recovery needs no SQL edits; unknown charge remains reserved. | A1b,A8a; M |
| A10 | Shared configuration: `pipeline_config` (new), `extraction_core`, `background_pipeline`, `E/runner` (R3.13). | App/harness bytes identical, including context stage; settings survive validation; unsupported settings rejected. | A4; M |
| A9 | Explicit generation identity in `pipeline_config`, `E/{gateway,store}` (T§4). | Change effort/model/thinking/tokens → cache miss; served model, usage, stop reason retained. | A10; S |
| A11 | Storage control in `E/openai_adapter` (R3.12). | Capture Responses and Chat payloads → explicit `store:false`; execution uses foreground Responses. | —; S |
| A12a | Stage reasoning/token floors in `pipeline_config`, `E/{openai_adapter,gateway}`. | Effort reaches payload; floor change misses cache; mocked rotation returns valid label. | A9; S |
| A12b | Outcomes: `E/{openai_adapter,openai_client,gateway}`. | Empty/refused/truncated → charged failures; explicit wordless success valid. | A8a,A12a; S |
| A13 | Cache tariffs/lifetime in `ai_pricing`, `E/{gateway,openai_adapter}`. | Four tariff fixtures reconcile; `ttl=30m` affects identity; write price replaces input price; unknown usage stops further dispatch. | A1b,A9; S |

**Added on review (orchestrating session).** One item the list omits and the analysis plan requires: **P1**, snapshot the 57 pilot books (ids, records, page images and the extraction-error log) into the evidence folder, and list the fragments and same-work pairs the review found. About 1,000 Firestore reads plus the image downloads, so it needs Chris's go-ahead. Accepted when the counts match the census. Depends on A2a; do it before B3a deploys or after B3b, so deleted records are not captured as reference; S.

## B. Before `claude-dev` can go to `main`

| ID/reference | Change and files | Runnable acceptance → expected result | Depends; size |
|---|---|---|---|
| B1/R2.1 | Production prompts: `P/ai_prompts`, `extraction_core`, `utilities`. | Stub stages → old production hashes, distinct from November baseline if different. | A4; S |
| B2/R2.2 | Per-upload workers: `background_pipeline`. | Two mock uploads start before either finishes; batches retain serial queue. | —; S |
| B3a/R2.3 | Reactivation in `data_structures/{character,alias}`, `pages/enter_text`. | Delete/re-add each entity → visible once, no collision; unrelated rejected aliases remain rejected. | —; S |
| B3b/R2.3 | Filter rejected: `E/snapshot`, `pages/results_dashboard`. | Rejected character/alias absent from reference/totals; accepted records remain. | —; S |
| B4/R2.4 | Clear review cache in `pages/validation`. | Owner edits between reviews → reopening sees new text; forgetting clears cached pages. | —; S |
| B5/R2.5 | App-owned `text_normalization` (new); update `alias_schema`, `alias_pipeline`, `P/pipeline_prompts`, harness. | Disable harness imports → app/rendering works; shared outputs unchanged. | —; S |
| B7/D018.1 | Gender help in `P/forms`. | Inspect rendered help → textual cues include gendered words; no illustrations/stereotypes; no pronoun-only rule. | —; S |
| B6/R2.6 | Ten-line `planning/deploy_checklist.md`. | Walkthrough covers B, T§5.1 comparison, cookie reset, unsafe post-deploy `rename_book.py`; Chris deploys. | B1–5,B7; S |

## C. Later — listed only

| ID/reference | Change and files | Future acceptance → expected result | Depends; size |
|---|---|---|---|
| C1/R5.1,3,4,8 | Record status/model/source/version: `data_structures/`, `background_pipeline`, `book_reconstruction`. | Failure differs from wordless success; provenance/flags survive reload. | A8a,A9; M |
| C2/R5.2 | Strict production parsing in `extraction_core`, `alias_pipeline`. | Missing/invalid fields rejected, never defaulted to asserted codes. | A10,C1; M |
| C3/R5.5 | Missing-value export in `E/snapshot`, `pages/results_dashboard`. | Never-coded fields export null; known false stays false; unvalidated imports identified. | —; S |
| C4/R5.6 | ISBN/edition/work ids in `data_structures/book`, `P/forms`. | Editions remain distinct under one work id; absent identifiers remain missing. | —; M |
| C5/R5.7 | `CODEBOOK.md`, `DATASHEET.md`. | Export walkthrough covers definitions/provenance/missingness/limitations. **C:** university release/consent/licence decisions before release. | C1–4; S |

**Order/parallelism.** First A2a prevents loss; A2b recovers reproducibility; A6a fixes selections. A6b then unlocks A3a/b in parallel, followed by A3c: scans/references/mapping are the likely long pole. Provider, synthetic scoring and B work proceed alongside; serialize shared-file edits.

**Branches (Chris, 11 November; D018.8).** After H, six branches off updated `claude-dev`, one commit per item, each verified before the next: `impl/evidence-selections` (A2a–b, A6a–b, P1), `impl/inputs-prompts-examples` (A3a–c, A4, A7a–b), `impl/measures` (A5a–f, A8a), `impl/providers-config` (A1b, A8b, A9–A13), `impl/main-merge-fixes` (B1–B7) and, later, `impl/corpus` (C1–C5). Six merges, each needing Chris's specific approval. No auto-push; Chris alone handles `main`.

**Cuts.** Retain all thirteen: each prevents observed failures or plausible accidental mischarging/exposure. Share helpers for 9/10 and 11–13; keep checks separate. Production status/strict parsing stays C; adjudication/old zero-margin gates stay cut. T§5.6: predeclare target; Iterate gain >2×its floor, no primary/guardrail loss >own floor; Check target/primaries lose no more than Check's floor. Cost increases need stated gains.

**First probes, each provider.** All A offline checks, including 11–13, pass; account setup, balances/cycles and current settings/prices verified. Synthetic-only probe has an explicit small ceiling, reserved within separate min($150 Anthropic/$200 OpenAI, actual allowance) caps; no purchases/transfers. Reconcile ledger **and intended allowance**, stopping on unexplained charges: this completes A1a. Both probes must settle before two Iterate scan-book cost/failure checks, then $15/provider screens. Rehearsal/proof remain uncommissioned.

## Open questions for Chris

None remain for this implementation list.
