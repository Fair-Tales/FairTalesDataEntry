# Review: value, method and reusability of the tuning runs (17 November 2026)

Astra (gpt-6-astra, ultra effort), read-only, after the baseline and before any further paid run. Requested by Chris. Verbatim apart from link paths; claims about the freshness check, the shared prompt setting, the score identity and the 53% figure were rechecked against the code. Chris's decisions on it follow the review.

**A — Value: the $79.59 baseline spend was useful; the proposed next programme is too expensive for the evidence it would buy.**

**B — Method: sound exploratory foundations, but independence, variance assumptions and measurement scope need correction before formal acceptance decisions. Agreement alone cannot establish that routine review is unnecessary.**

**C — Reusability: good raw evidence capture, but preservation, scoring versions and corpus exports are not yet sufficient for a portable research release.**

This was a local, read-only review. No files changed, network requests or paid calls were made; no per-book Check records or sealed content were opened.

The old arm’s measured spending shows where savings matter:

| Component | Cost per paid book | Share |
|---|---:|---:|
| Transcription | $0.463 | 63% |
| Sonnet image checks | $0.229 | 31% |
| Character detection | $0.014 | 2% |
| Other calls | $0.031 | 4% |

The baselines exposed serious truncation and schema failures and established performance on photographs. That was valuable. However, the new character prompt’s roughly elevenfold cost increase makes the original “cheap character iterations” rationale obsolete. Its completed outputs were also no better overall. [Baseline diagnosis](../planning/baseline_runs_2026-11-16.md:95)

Ranked recommendations:

1. **Make the next comparison genuinely independent and character-only. Cost: $0 API; effort: small; before next paid run: yes.**

   `cf948bc` passes the requested static audit: both transcription and character requests carry their labels through `create_message`; neither silently falls back to the model label. Without the option, request identities remain unchanged.

   Two surrounding traps remain. New output directories using old replicate numbers can replay calibration responses while passing the “fresh runs” check; an in-memory synthetic check confirmed this. Use unique phase/arm replicate tokens and check independence across calibration and comparisons. [Freshness checks](../scripts/extraction_eval/tuning_loop.py:207)

   Also, `prompt_variant` controls both transcription and characters. Switching it to test character rules changes upstream requests; selective replication does not freeze changed bodies. Isolate the character change and verify upstream cache reuse before dispatch. [Transcription setting](../extraction_core.py:86)

2. **Buy a small diagnostic screen next, not the full replication programme. Cost: approximately $2 for twelve candidate character calls at observed rates; effort: hours; before full calibration: yes.**

   Keep identical cached text and compare the candidate with existing outputs on 6–12 Iterate books. Include known truncation cases. This can reject an expensive failure cheaply; it cannot establish general improvement.

   R=20 and r=4/8 come from simulations, not measured candidate variance. R=5 gives only four variance degrees of freedom. Start with a predeclared smaller diagnostic calibration, then choose formal counts from observed noise and a worthwhile gain—not repeated testing until something passes.

   Nineteen additional legacy character passes project to about **$20**, or **$29.45** using the conservative $1.55 allowance. The allowance is explicitly unmeasured. Adding the proposed **$35–65** comparison exceeds the **$53.25** remaining. Defer the **$140** transcription calibration until a viable transcription change exists. [Allowance](../scripts/extraction_eval/README.md:1251)

   Correct renewal bookkeeping to the **29th**: both ledger settings still say day 1. Preserve existing spend and reservations when correcting it.

3. **Repair the interpretation of the acceptance rule. Cost: $0 API; effort: several hours; before formal candidate acceptance: yes.**

   The rule uses incumbent noise for both arms and does not enforce that calibration belongs to the incumbent configuration. Medium effort and changed prompts need variance evidence; Student-t thresholds do not cure unequal variances. [Implementation](../scripts/extraction_eval/tuning_loop.py:223)

   Passing a noise-based deterioration threshold means “harm was not detected”, not “no material harm”. Keep loop decisions exploratory. A 2.5% false-gain probability per attempt is not protection across thirty attempts: under independent null comparisons, the chance of at least one false gain is approximately **53%**.

   Fresh final-versus-baseline runs address response-selection luck, not overfitting to Iterate books. Check is correctly treated as tuning. Leave-one-book-out examples are implemented, but cannot undo prompt development on those books. Preserve rehearsal/proof and all rejected attempts; no elaborate multiple-testing machinery is necessary if publication claims rest on the untouched proof.

4. **Settle what counts as correct before optimizing it. Cost: $0 API; effort: hours; before transcription tuning or alias acceptance: yes.**

   “Inserted text” means absent from the reference, not necessarily invented. Correct picture lettering can lose points; deleting it could improve scores while impoverishing the corpus. Preserve raw output and distinguish historical-transcript agreement from the intended analytical text scope. [Diagnosis](../planning/baseline_runs_2026-11-16.md:180)

   Character recall does not penalize extra characters. Alias groundedness checks that wording occurs, not that it belongs to the correct character. Apply the modest alias-accuracy sample already required by decision 021 before accepting additions. [Decision](../DECISIONS.md:829)

   Attribute scores use separate favourable matchings, so they are not complete-character correctness. Shared harness/app helpers provide good computational parity, but app retries and stored settings differ. Publishing these measures as autonomous, end-to-end accuracy would be unconvincing.

5. **Preserve the paid evidence and version every rescore. Cost: $0 API; effort: roughly half a day; before next paid run: preservation yes.**

   Raw responses, predictions, settings, dependency versions and source hashes support offline rescoring. But `evaluation_runs/` is gitignored and local; another backup was not established. Preserve a consistent SQLite backup, run directories, exact code, governed references and a short replay note in existing private storage. Copying only reports—or a live SQLite file without its WAL—is insufficient. [.gitignore](../.gitignore:264)

   External Word references remain necessary. The bundled API references are under volatile `/tmp`; simulation evidence is linked under `.cache`. Preserve the supporting material actually relied upon.

   Scoring identity currently lacks the current scorer’s source hash. Record it and write changed scores to a new report directory; otherwise different measurements can retain the same identity. [Score identity](../scripts/extraction_eval/cli.py:541)

6. **Optimize large surviving workloads, proportionately. Effort: small caching change; moderate batch integration; before next small screen: no.**

   The bundled reference specifies **50% Message Batches pricing**, up to **100,000 requests or 256 MB**, maximum **24-hour** processing and results retained **29 days**. Conditional savings are **$18–33** on the proposed comparison or **$70** on transcription calibration. Preserve local accounting/results; do not build a general scheduler for this budget. [batches.md:3–11](/tmp/claude-1000/bundled-skills/2.1.322/98c07a88497fde2ff3d554084d8bd7d5/claude-api/python/claude-api/batches.md:3)

   Fixed-prefix caching is smaller value: observed candidate character spending was **92% output**, so even eliminating all input charges saves under **$5 across 380 calls**. Actual savings are lower. Sonnet 5 requires a **1,024-token** prefix; reads cost **0.1× input**, five-minute writes **1.25×**. Put the breakpoint before book-specific content and preserve example exclusions. [Minimum/prices](/tmp/claude-1000/bundled-skills/2.1.322/98c07a88497fde2ff3d554084d8bd7d5/claude-api/shared/prompt-caching.md:131), [placement](/tmp/claude-1000/bundled-skills/2.1.322/98c07a88497fde2ff3d554084d8bd7d5/claude-api/shared/prompt-caching.md:54)

   Screen cheaper image checks locally on admitted Iterate cases: halving their cost would save approximately **$8.60 per fresh 75-book pass**, assuming unchanged quality.

7. **Make the corpus export joinable and typed. Cost: $0 API; effort: about half a day; before analyst handoff: yes, before calibration: no.**

   Current provenance and missingness handling are useful. However, CSV exports omit document IDs, stringify nested evidence and collapse null/absent/empty values. Add record IDs and a typed JSON/JSONL companion with run and schema versions. Local predictions are not automatically corpus records. [Export limitations](../CODEBOOK.md:27)

   A reviewer needs frozen methods, selection, configurations, scoring versions and accessible supporting evidence. Deposit scope, licensing and account-name handling remain unresolved; internal reuse can proceed meanwhile. [Datasheet](../DATASHEET.md:75)

**Open questions for Chris**

- May small Iterate-only screens eliminate failing candidates before full repeated comparisons? Recommended: saves money on obvious failures. Keeping the current schedule buys full repeated evidence for every candidate.
- Should the corpus retain picture lettering with a filtered analytical view, or reproduce the narrower historical transcripts? Retention preserves future research options; narrower extraction simplifies immediate agreement scoring but loses material.

## Chris's decisions (17 November)

- **Picture lettering:** keep it, tagged as its own kind of text (DECISIONS 025).
- **Screening and the plan:** not yet decided. Fable reviews this review and the plan first. Chris's steer: keen on small screens, on iterative prompt development on small numbers of books to test new ideas before scaling up, and on batch mode wherever possible to save money.
- **Backing up paid run evidence:** later; to be raised again.
