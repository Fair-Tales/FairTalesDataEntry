# Pipeline second-half implementation — 5 November 2026

Implemented and tested offline. No inference, provider requests, service access,
git commands, baseline report edits or snapshot edits were performed. The new
candidate configuration is `scripts/extraction_eval/candidate_pipeline_v2.json`;
it retains the baseline extraction/rotation/crop models, image settings and OCR
output ceiling. The frozen baseline configuration remains unchanged.

## Versions and retained evidence

`conventions.py` records `pipeline-conventions-v1`, with separate rule versions:
C1, C2 and C6 remain provisional; C3 and C4 are accepted. C3's entity-population
projection remains provisional. C7 (`evidenced-short-category-collective-v1`) is
an additional provisional convention implementing the owner's newer coverage
instruction. It permits an additional bare contiguous printed referring head
inside an evidenced fuller phrase, explicitly tagged `embedded_short_form` and
retaining the fuller phrase/owner evidence. It never strips possessives at match
time. Category/hypernym words must themselves occur in the supplied text and
refer in context. A group occurrence belongs to the group; membership alone does
not make it an individual alias. Uncertain attachments retain candidate IDs.

Archival text, regions, provisional page labels, book-context decisions, explicit
credit roles, all publication events, raw character outputs and unresolved
mentions are retained. C1 lettering inclusion, C2 page-kind inclusion and C6
reteller inclusion can be changed by pure projections without inference.
Student text and old publication values remain preserved. Page evidence has a
Field-backed sidecar; invalid or truncated outputs retain raw audit evidence.

`pipeline-evidence-v1` contains verbatim development-only quotations, source
page IDs and text/image hashes, with photographic verdicts distinguished from
provisional text-derived examples. Exact rendered UTF-8 prompt strings and
hashes accompany the output/request provenance. `rules-only` and `rules-plus-icl`
are explicit prompt variants. The exposure ledger retains all 34 exposed IDs,
including both Gruffalo books and the incidental Letter to Santa exposure.
No held-out prose was opened for examples or tests.

## Publication and evaluation

`bibliography-v2` retains mandatory photographed-edition and first-publication
targets, explicit imprint and publishing-house fields, scoped events and the
legacy archive. It never infers an original event from an old unscoped field.
`scripts/migrate_publication.py` defaults to dry-run, prints read/write budgets,
uses update-time preconditions and preserves populated/manual fields. Physical
legacy fields remain intact; versioned export projections use only evidenced
scoped values. No migration was executed against Firestore.

The comparison registry covers known-positive variants/aliases/characters,
M1–M4 diagnostics, separately evidence-eligible identity errors, OCR/story,
credits/publication facts, regression fixtures and operational coverage. It
uses per-book counts, paired cluster resampling and explicit undefined results.
It rejects mixed scoring/reference/overlay protocols and emits raw-to-raw,
corrected-to-corrected and fixed-baseline measurement-repair comparisons.
Reference alias precision is diagnostic only, never an acceptance gate.

Dev20 aliases-only recall is a smoke test: equivalence normalization credits
6/10 across five eligible books, while strict raw-surface comparison credits
4/10. Ten positives cannot establish an improvement. The pooled historical
variant recall primarily measures names. In the cached predicted Pea text,
`fruit` is absent: this implementation does not invent it to improve recall.
True mention attribution and population false-merge/false-split rates remain
unmeasured; dictionary uniqueness does not establish correct identity.

## Orchestrator handoff

The runner requires a dedicated `EVAL_ANTHROPIC_API_KEY` and a frozen billing
attestation containing subscription-credit funding, organisation, project,
confirmer/date and the exact key SHA-256. The account identifiers are owner
attestation, not remote billing verification. A mismatched/missing binding
fails before client creation. There is no production/environment-key fallback.
Credit exhaustion or billing/auth rejection stops dispatch immediately, persists
completed work with an explicit incomplete run, returns a failing exit status,
and does not automatically retry or switch keys. The candidate config remains
unconfirmed until the orchestrator fills the actual account binding.

Evaluation ISBN enrichment replays frozen answers only. Set `frozen_lookup_dir`
to the approved local lookup cache if required; missing cached answers remain
explicit failures. Scoring the baseline must use a new `--out` directory so its
frozen reports remain untouched. This task did not run the approved paid dev20
re-score or any model/rotation/holdout experiment; those remain orchestrator work.
Browser/service-backed E2E tests require `FAIRTALES_RUN_E2E=1`; ordinary pytest is
offline and retains the original non-loopback socket blocker.
