# OpenAI credit options — 5 November 2026

Design only. This document proposes no immediate spend or deployment. Evidence comes from local source, existing development artifacts and the facts Chris supplied. No browsing, provider/API calls, git commands or tests were run. Only this document was created. **UNVERIFIED** means a fact still needs the specific check stated below; a proposed budget is not spending authorization.

## Recommendation

**Deliberately leave the OpenAI credits unused for now. Protect the live app's Anthropic allowance first, and finish the already-approved Anthropic experiment.** A second provider is several days of implementation followed by continuing maintenance. There is no measured OpenAI quality advantage, no recurring OpenAI workload established here, and the two most obvious “savings” do not survive inspection: the Anthropic holdout cannot move providers, while ordinary forward photo ingestion makes zero classifier calls.

The **runner-up is one bounded, evaluation-only GPT comparison**, starting with the measured OCR failure cohort. Its purpose would be to decide whether provider diversity improves useful coverage enough to justify further work. If Chris wants that research question answered, propose ceilings of $5 for the initial diagnostic, $25 for development-book evaluation, and a separately gated $50 for a frozen held-out arm: **at most $80 of OpenAI credits**, with no obligation to spend the other $120. These are future credit-spend limits, not cost predictions or approvals. Current prices, actual image billing and account limits must be checked first. To share the planned 40 holdout books, choose and freeze the GPT arm **before Anthropic holdout unblinding**; otherwise keep it development-only until a separately authorized untouched validation design exists.

Production failover is a possible later operational use, but **a working fallback needs both measured quality and mandatory provenance**. It should not be the first experiment on a live research corpus. Declining OpenAI does leave the Anthropic outage risk unresolved: the immediate design priority is a production reserve, stopping discretionary evaluation before it consumes that reserve, and a recoverable manual/paused mode when funding fails. Those improvements are worthwhile even if no second provider is ever added.

Ranked by value relative to maintenance burden, under the workload evidence available today:

| Rank | Option | Credit cost | Maintenance and verdict |
|---|---|---|---|
| 1 | Deliberately decline; protect Anthropic production | OpenAI $0 | No second prompt, pricing or failure surface. Best present choice; accept expiry. |
| 2 | Controlled GPT arm, initially targeted OCR reliability | Proposed OpenAI ceilings $5 + $25 + $50; conditional cost examples below | A substantive research question, but shared adapter/accounting work is unavoidable. Keep outside production. |
| 3 | Production exhaustion failover | Demand-driven; must reserve OpenAI capacity rather than spend it on experiments | Real operational benefit if outages threaten the pilot; greatest implementation and continuing burden. Conditional on validation and provenance policy. |
| 4 | Small research-assistance workflow, such as a cited literature digest | Proposed cap $10/month; text-only examples below exclude tool fees | Can recur, but no established unmet need; pilot only if it saves Chris time. |
| 5 | Optional legacy photo classification | Haiku estimate $0.268992/session; OpenAI unmeasured | Maximum saving is pennies. Reject as a reason to add a provider. |
| 6 | “Offload the approved evaluation” | No legitimate replacement of its Anthropic inference spend | Reject this framing. Provider-neutral scoring already costs $0 in API usage. |

OCR reliability and a GPT arm are related stages of one investigation, not two independent excuses to build the same adapter. Rankings should change if measured pilot demand makes uninterrupted AI processing essential; unused credits alone are not evidence of that need.

## Two balances, one live-app constraint

Accept the supplied [OpenAI benefit terms](https://developers.openai.com/api/docs/guides/chatgpt-api-credits) as given, without re-verification: Chris's Pro $200 subscription supplies $200 per billing cycle, linked through Billing to one API organisation he owns after the seven-day eligibility period. Trials/complimentary subscriptions do not qualify. Credits expire without rollover; a payment method is not required while active credits remain and becomes necessary once exhausted. Responses, Agents, Decisions, GPT-Live, Realtime, Images and the stated built-in tools are covered; ordinary organisation rate limits still apply. **“Codex usage through API keys is not supported by this benefit.”** Astra's subscription-backed `codex exec` is separate. None of the options funds Codex via an API key.

The Anthropic balance is independently $200/month, already linked and working. The successful $0.000034 Haiku probe and production key's use of that balance are supplied facts. With no payment method attached, exhausted Anthropic credits stop API requests. **Two allowances are not a fungible $400 production budget**, and separate evaluation keys in the same organisation do not isolate the balance. Neither current remaining balance nor either renewal date has been established here; do not assume calendar-month resets or matching cycles. OpenAI linking/claim completion and its payment-method status are also not established.

| Anthropic commitment | Credit dollars | Status |
|---|---:|---|
| Dev20 work described as re-score | ~$18 | Approved, not run |
| Paired 40-book Anthropic baseline + candidate, reusing rotation/crop requests | $57.729674 | Approved in principle, contingent on re-scored dev20 results |
| Combined planning commitment | ~$75.729674 | Preserve both arms and their funding |
| Nominal remainder from a fresh $200 allowance | ~$124.270326 | **Before** other spend, live usage and contingency |
| Remainder without rotation/crop reuse | ~$114.865232 | Holdout would cost $67.134768; reuse saves $9.405094 |

Let `S` be all other spend already incurred or reserved in that Anthropic cycle, including the probe if in that cycle. Available production capacity after these commitments is **at most `$124.270326 − S`**, not a verified balance. The verified $0.7522596/book gives `$200 / $0.7522596 = 265.87` book equivalents, or **265 complete books** at exactly that mean. It is a development workload average, not a guarantee for different production books, retries or stages.

A concrete planning allocation is $100 for remaining live work and $20 contingency. With the approved work, this leaves only **$4.270326 − S** unallocated in a fresh cycle. This is an illustrative reserve, not measured pilot demand. Set the real reserve from expected remaining pilot volume and observed production costs. If it cannot fit, defer the holdout to a renewed allowance after dev20 review, reduce discretionary work, or have Chris choose a paid-continuity policy. Do not replace an experimental arm with GPT to make the arithmetic fit.

Admission control must account for production and evaluation together, including in-flight reservations and unknown-cost calls. Existing local evaluation budgets cannot police independent live calls. Until a shared cycle budget exists, use a conservative owner-maintained allocation and avoid overlapping large evaluation runs with pilot work. Keep provider dollar totals, credit consumption and any cash billing distinguishable; zero marginal cash does not mean zero API cost.

Expiry favours *useful recurring work*: live fallback during a busy pilot or an already-needed monthly research digest. This month's comparison and a finite photo backlog are one-off workloads. Never rerun unchanged experiments, broaden the holdout, or manufacture tasks to avoid forfeiture. If failover is adopted, deliberately unused OpenAI credit is the reserve that makes it useful; draining it just before Anthropic fails defeats the design.

## What the repository actually supports

The following were inspected directly. These are constraints on a future implementation, not changes made in this round.

| Evidence | Finding and design consequence |
|---|---|
| [gateway.py](../scripts/extraction_eval/gateway.py), lines 103–150; [cli.py](../scripts/extraction_eval/cli.py), lines 128–135 and 190–191 | `provider` enters request identity at line 126, defaulting to `anthropic-messages`; dispatch still calls `client.messages.create`, and CLI construction is Anthropic-specific. This is a cache-identity provision, **not a second provider adapter or config switch**. |
| [ai_pricing.py](../ai_pricing.py), lines 40–88 | Anthropic-only prices and cache multipliers 0.1/1.25. Unknown models return $0 with a warning. `_usage_tokens` reads Anthropic-shaped fields. |
| [utilities.py](../utilities.py), lines 496–586 | `record_api_usage(response, model, label="")` uses those buckets and prices; missing usage may be ignored. Live rollups group by model/flow, without a provider dimension. Reusing it unchanged for OpenAI can undercount, overcount or record zero. **That is an accounting correctness bug and invalidates budget claims.** |
| [baseline.json](../scripts/extraction_eval/baseline.json), pricing block | Frozen baseline, `pricing.verified=true`, timestamp `2026-11-04T23:03:19.304138+00:00`, and source attesting verification against docs.anthropic.com and the shared table. Retain this baseline unchanged. Older introductory-price comments elsewhere do not supersede this dated evidence. |
| [gateway.py](../scripts/extraction_eval/gateway.py), lines 45–98 | Validator requires positive input/output/cache-read/**cache-write** rates and model limits. Ceiling calculation allows Anthropic request keys and counts Anthropic image blocks. Settlement reads Anthropic usage. Merely adding a GPT row would not make dispatch, reservations or charging correct. |
| [CLAUDE.md](../CLAUDE.md), convention #129; [utilities.py](../utilities.py), lines 325–344 and 720–799 | Clients and vision request/parsing logic must be shared. Add **`utilities.get_openai_client(*, api_key=None, max_retries=None)`**, mirroring explicit account-bound injection and missing-key behaviour. Keep `vision_text()`/`vision_json()` as the sanctioned vision entry points, backed by one provider adapter. No inline OpenAI construction. |
| [utilities.py](../utilities.py), `_claude_json`, `locate_cover_pages`, `lookup_person_details`; [extraction_core.py](../extraction_core.py) | Some text, cover-location and tool requests call Messages directly; callers catch Anthropic exception types. A fallback only inside `vision_json()` does **not** keep the entire app working. Define covered flows and adapt their callers deliberately. |
| [billing.py](../scripts/extraction_eval/billing.py), lines 17–52 | Evaluation binds a separately named `EVAL_ANTHROPIC_API_KEY` to an attested organisation/project. Its broad billing-stop detector treats all 401/402/403 alike. Suitable for stopping work conservatively, **unsuitable as proof of exhausted funds for automatic substitution**. |
| [comparison.py](../scripts/extraction_eval/comparison.py), metric registry and protocol identity; [protocol_v3.json](../scripts/extraction_eval/protocol_v3.json) | Current comparison already includes field-aware endpoints and scorer/schema/convention/overlay identity. Reuse this code; older prose describing an OCR-only comparison is stale. |
| [local_models.py](../local_models.py), opening contract | An Ollama client uses an OpenAI-compatible format for a separate importer path. It is not an OpenAI Responses adapter, credit-account binding or metered evaluation/production integration. It does not remove the work above. |

Python 3.10 is the documented project target in CLAUDE.md. The supplied suite status is **1,132 passed, 27 skipped**; it was not independently rerun or verified here and is not evidence that a hypothetical adapter works. Future SDK selection must support Python 3.10, and future implementation must run appropriate accounting/provider tests and the required regression suite.

### Shared work required by any extraction-related OpenAI option

Plan roughly **4–7 focused maintainer days** for a bounded offline adapter, usage/pricing changes and harness integration, plus **1–2 days** to prepare and review the controlled experiment. Production routing, durable provenance, UI/recovery and rollout add roughly **4–8 days**. These are scope estimates, not commitments; unfamiliar SDK behaviour or persistence migrations can extend them. A prototype request is quick; a trustworthy metered second pipeline is not.

The shared implementation must cover request construction, JPEG/data-URL encoding and image limits, Responses output parsing, refusal/incomplete/truncation states, schema validation, exception normalization, bounded retries, SDK retry disabling where the gateway meters attempts, and raw-response/request-ID persistence before parsing. Extend runtime injection and the CLI's provider/account selection. Text stages must use the same adapter and accounting discipline. Keep caches and reservations distinct by provider, endpoint, model snapshot, request/prompt version, image detail, replicate and request bytes. An Anthropic response cannot be reused as if GPT produced it.

Extend **both** `ai_pricing.py` and `utilities.record_api_usage`, including the accumulator and evaluation settlement. Store raw provider usage and a versioned normalized record: provider/endpoint, requested and returned model, total input, cached input, uncached input, output, price version, cost status and USD charge. Responses conventionally exposes `input_tokens`, `output_tokens` and nested `input_tokens_details.cached_tokens`; verify the exact selected SDK/API schema before implementation. Some top-level names overlap Anthropic's, which makes partial accidental compatibility especially dangerous. Cached input is a subset of total input in this design, so charge:

```text
OpenAI USD = ((total_input − cached_input) × ordinary_input_rate
              + cached_input × cached_input_rate
              + output_tokens × output_rate) / 1,000,000
```

Do not apply Anthropic's 0.1/1.25 multipliers, add cached tokens on top of already-total input, or double-charge reasoning tokens if already included in billable output. Preserve additional billable units if enabled. Missing/invalid usage or an unknown price means **unknown cost with retained reservation and reconciliation**, never $0. The live UI can retain a successful draft during an accounting fault, but must surface the fault and stop admitting new discretionary paid work until accounting is reliable. Reconcile local totals to organisation usage before trusting a pilot estimate; keep an outstanding liability for possibly billed timeouts. A cache replay must not charge twice.

The new arm's separate pricing block needs `verified`, `verified_at`, official source URL(s), provider/endpoint, exact model/snapshot, currency and units, ordinary/cached-input/output rates, applicable context/service-tier rules, image accounting and request limits, and the account to which the checks apply. Pin actual account rate limits separately from model context limits. Explicitly disable unpriced tools or modes. **The current schema/validator cannot honestly represent a non-applicable cache-write bucket:** it requires a positive number and has Anthropic settlement semantics. Introduce a versioned provider-specific schema with zero/not-applicable distinctions and corresponding reservation/usage code; never invent a positive cache-write price to pass validation. Keep `verified=false` until every required check is complete.

Acceptance tests should cover cached versus uncached charges, malformed/missing usage, returned-model mismatches, SDK retries, refused/truncated replies, 429 versus funding errors, uncertain outcomes, replay, and provider-distinct request identities. Production adds concurrent failover and provenance/persistence failures. These are future implementation requirements; none was executed here.

## Costs: what can honestly be calculated offline

**No OpenAI price is verified as current on 2026-11-05.** The following are remembered published standard rates, used only for transparent sensitivity calculations. They are not quotations retrieved today, an assertion of current availability, or a recommendation to choose these models over newer ones.

| Reference model | Ordinary input / million | Cached input / million | Output / million | Status |
|---|---:|---:|---:|---|
| GPT-4.1 | $2.00 | $0.50 | $8.00 | **UNVERIFIED** current price, availability and account access |
| GPT-4.1 mini | $0.40 | $0.10 | $1.60 | **UNVERIFIED** current price, availability and account access |

To settle this, later open the official [API pricing page](https://developers.openai.com/api/docs/pricing) and each selected model's official documentation; record current standard Responses rates, snapshot availability, image-detail/token rules, context tiers and any billable extras. Check the linked organisation's Billing/Usage and Limits settings for applicability and access. These links are check destinations, not sources fetched during this design. Do not schedule paid work from this table alone.

The existing development artifact `dev20-run/calls.json` under the `ART` location documented in [pipeline improvement design §1](pipeline_improvement_design_2026-11-05.md#1-evidence-terminology-and-decisions) was inspected for aggregate usage, without inspecting held-out content. It contains **1,669 attempts: 1,666 completed and three failed**, $15.045192 recorded cost, **6,520,266 input and 103,605 output tokens**, and zero reported cache tokens. This independently reproduces the supplied mean. Means per book are 326,013.3 input and 5,180.25 output tokens.

Holding those **Anthropic** token counts and call patterns fixed gives:

| Hypothetical mapping | Conditional USD/book | 20 books | 40 books | Whole-book equivalents / $200 |
|---|---:|---:|---:|---:|
| Verified Anthropic baseline | $0.7522596 | $15.045192 | $30.090384 | 265 |
| Every stage GPT-4.1 | $0.6934686 | $13.869372 | $27.738744 | 288 |
| Every stage GPT-4.1 mini | $0.13869372 | $2.7738744 | $5.5477488 | 1,442 |

For example, `(326,013.3 × $2 + 5,180.25 × $8) / 1,000,000 = $0.6934686`. These are **UNVERIFIED fixed-token equivalents, not measured OpenAI per-book prices or quality-equivalent capacity**. OpenAI image metering, tokenizer, output lengths, batching limits and failure branches differ. If input usage were twice the Anthropic count at fixed output, the same full-model example would cost $1.3454952/book, or only 148 complete books/$200. This illustrates sensitivity, not an upper bound. A mixed full/mini mapping is another experimental variant, not free savings.

The honest present answer to “how many OpenAI books?” is **unmeasured; 288 is one explicit conditional scenario**. After implementation and authorization, use a few development books to measure every stage, per-book distribution, failures/retries, and actual uncached/cached usage. Replace the scenario with measured USD/book and conservative admission reservations before increasing volume. Budget limits must reserve worst-case allowed requests and in-flight liabilities, not merely stop after a mean-cost target has been exceeded.

## Distinct options assessed

### 1. Nothing / deliberately decline

Cost: $0 OpenAI; no new secret or production dependency. Up to $200 expires monthly, but this is forfeited capacity rather than money that should be recovered through unnecessary work. Declining can mean not claiming/linking yet, or claiming without creating a new pipeline; neither creates a useful workload by itself.

This preserves one prompt surface, one price/usage model and one failure taxonomy. The counterargument is real: it buys no provider redundancy and answers no comparative research question. Choose it while the approved Anthropic work and deterministic improvements have stronger evidence of value. Reconsider when a bounded comparison has an explicit scientific purpose, or pilot volume makes the operational benefit worth the maintenance. Do not describe the current no-card production setup as safe merely because $200 was recently available.

### 2. Production resilience / exhaustion failover

Cost: demand-dependent. At the conditional full-model rate, 20 or 100 fallback books would consume about **$13.87 or $69.35** OpenAI credits; neither is a measured forecast. Failed/uncertain Anthropic attempts, successful earlier stages and later retries retain their own Anthropic costs. Reserve OpenAI credit for the anticipated fallback workload and prevent research jobs consuming it. If both allowances are exhausted, the app must still preserve work and enter manual/paused operation. Verify OpenAI renewal and available funding before enabling this feature.

**Detection must classify the actual exception, not poll a balance.** Define a provider-neutral `ProviderFundingExhausted` outcome only after a provider-specific parser positively identifies an insufficient-credit/funding rejection in a structured SDK error. The exact current Anthropic HTTP status, SDK exception class and error-body signature are **UNVERIFIED**. Settle them from current official error documentation and an existing redacted, confirmed billing rejection fixture, if available; otherwise require validation before enabling automatic routing. Do not deliberately empty the production account to obtain one. A status/class alone is insufficient; an Anthropic `BadRequestError`/400, for example, can represent many unrelated problems.

| Error category | Proposed behaviour |
|---|---|
| Positively identified exhausted-funding rejection | Persist the event; open an organisation-scoped circuit; route only validated, approved flows to OpenAI and record every attempt. |
| 401 authentication, 403 permission, invalid model/schema or other ordinary 4xx | Surface configuration/request failure; no automatic provider substitution. |
| Ordinary 429 rate limit | Respect the metered retry policy/backoff; do not infer an exhausted balance. |
| 5xx, transport timeout or connection failure after possible dispatch | Preserve uncertain cost and outcome; bounded recovery according to policy, without an automatic duplicate cross-provider request. |
| Refusal, malformed JSON or truncated completion | Preserve failure provenance and follow the stage's declared review/retry policy. This is not a funding trigger. |

Do not reuse `billing_rejection()` unchanged: its broad stop-on-auth-error behaviour would conceal broken credentials behind a different provider. The circuit must coordinate concurrent sessions and workers, prevent every page probing a known-empty account, and avoid oscillating providers within a job. Reset through an operator action or a specifically authorized recovery check after funding renewal, not balance polling. SDK-hidden retries must not bypass the budget ledger.

**Silent substitution is scientifically unacceptable. Provenance is mandatory.** OpenAI-derived material may coexist in Firestore only as explicitly attributed machine output. Persist stage/page-level provider, model snapshot, prompt/parser/convention versions, source image hashes/detail, request ID, timestamp, triggering failure and attempted-provider chain; connect derived characters/aliases to the text-stage provenance. A single book-level “GPT” flag is insufficient for a partly processed book. Retain human corrections and their audit history separately. Existing `Page.extraction_evidence` and `BatchBook.pipeline_job_id` are useful links, not a complete provider-provenance scheme.

Default policy: fallback creates **marked drafts requiring the existing review workflow**, not silently equivalent validated records. Show the degraded state in UI and exports. Commit result plus provenance atomically, or retain the result as a pending draft if provenance persistence fails; never publish an unmarked fallback record. Implement within the repository's Field/data-structure discipline and explicit transaction patterns. Historic records lacking evidence remain `legacy_unknown`; do not retroactively guess their provider. Analyses must stratify or explicitly model provider/mixed-pipeline status. Failover occurs at particular dates and workloads, so assignment is not random and must not be interpreted as a provider experiment.

Adding fallback behind `vision_text()`/`vision_json()` is only the vision portion. Character extraction and some other calls would otherwise still stop. Either validate and adapt those stages as well, or clearly offer OCR drafts with manual completion and describe that limited service. Do not claim uninterrupted full-pipeline extraction from a vision-only patch. Reuse already completed stages rather than restarting a whole book invisibly.

This option touches the production key/client path and adds `OPENAI_API_KEY` to Streamlit Cloud plus local development configuration. It offers useful pilot continuity, but low fallback volume means most credits intentionally expire. **Defer until the comparison establishes acceptable outputs and Chris chooses the research-record policy.** Meanwhile implement funding-error recovery/manual mode as its own future reliability task; this design does not implement it.

### 3. Offloading evaluation to reserve Anthropic for production

The approved 40-book comparison asks whether an **Anthropic candidate pipeline improves on an Anthropic baseline outside development books**. Replacing either arm with GPT changes provider/model together with prompt/pipeline. Books may remain paired, but the within-book difference no longer identifies the Anthropic candidate effect: the overfitting check's interpretation is destroyed. Moving both arms to GPT instead answers whether those variants differ on GPT; it still does not validate the Anthropic candidate. Therefore **none of the $57.729674 holdout inference budget transfers legitimately to OpenAI**.

The same rule applies to fresh Anthropic candidate inference within the ~$18 dev20 envelope. Pure rescoring of already-cached predictions, adjudication overlays, deterministic alias matching, bootstrap intervals, reports and fixture checks are provider-neutral and local: **$0 API cost already**. The supplied ~$18 remains the planning envelope for the approved work; do not mistake its “re-score” label for evidence that all of it is new inference or all of it can move. Inspect its execution manifest before dispatch.

GPT can assist with non-authoritative error summaries, synthetic software fixtures or literature notes if these are genuinely useful. Such assistance does not replace model-specific evaluation, deterministic scoring or independent human truth, and must not expose holdout content during development. Do not introduce GPT-as-judge, GPT-generated gold or a second prompt tuner and call it neutral evaluation. Reject offloading as a substantial source of Anthropic savings here. Reuse cached rotation/crop requests and schedule the actual Anthropic holdout around the live reserve instead.

### 4. A controlled GPT pipeline arm

This is the strongest positive use of the OpenAI benefit **if the comparison is itself a desired research output**. Preserve the existing comparison as A = Anthropic frozen baseline and B = Anthropic frozen candidate. Add G = the same frozen candidate prompts/settings/scorer as B with a declared GPT model mapping. **A–B remains the overfitting check; B–G measures the provider/model implementation under that pipeline.** Do not change prompts and provider together and then label the effect “GPT versus Claude”.

Use identical source images, deterministic preprocessing, semantic prompt text, available context, frozen bibliographic inputs, parser/schema, conventions and reference/scorer versions. Translate only the API envelope. Record unavoidable provider differences, such as image detail and output-limit semantics; failure of a request to fit a model's limits counts as failure unless a separately named variant was frozen in advance. Do not enable provider-specific tools, reasoning settings or structured-output modes for just one arm without declaring the additional treatment. No provider fallback within experimental arms.

For a full-pipeline comparison, GPT performs its own rotation/crop decisions too. Reusing Anthropic-produced decisions instead defines a **hybrid arm** and requires reporting the retained Anthropic cost. Both can be useful, but neither supports the other's claim. Any GPT preprocessing change needs the existing frozen rotation guards. Exact Anthropic cache reuse remains valid for A–B where requests match; no cross-provider cache substitution is valid for G.

Develop only on dev20 and other already-exposed fixtures. Preserve the usable holdout pool of **161 books: 90 alias-positive, 71 zero-alias**, and the planned 40-book sample of **27 positive + 13 zero**, frozen seed 20261105 and appropriate population weights. Respect [exposure.py](../scripts/extraction_eval/exposure.py). Freeze G, its endpoints and analysis before inspecting any held-out results. A third arm can use that same untouched sample without replacing A or B; its spend and purpose require separate authorization. If GPT design begins after unblinding those books, their results are development evidence for that design, not a new independent validation.

Use existing book-paired, stratified uncertainty calculations; account for additional comparisons and report undefined denominators and all intended failures. Record observed USD/book and latency as well as quality. Preserve primary aliases-only recall versus the known-variant diagnostic; zero-alias books do not supply aliases-only recall denominators. Reference precision is not a correctness target against this sparse reference, and M4 dictionary uniqueness is not speaker-attribution accuracy. Do not commission the cancelled annotation programme to rescue a weak claim.

The earlier **$13.869372/20 books and $27.738744/40** figures describe a baseline-shaped workload, not G's expanded candidate. The candidate adds context and larger prompts/evidence outputs. Applying the explicitly illustrative growth assumptions in [pipeline improvement design §6](pipeline_improvement_design_2026-11-05.md#6-model-and-cost-experiments) to the unverified full-model rates adds $0.068/book for context (30,000 input/1,000 output), $0.011 for characters (+1,500/+1,000), $0.0772 for OCR (+1,000 input across 38.6 pages), and $0.0117 for metadata (+1,000/+500 across 1.95 calls). That is **+$0.1679/book**, giving a candidate-shaped sensitivity of **$0.8613686/book, $17.227372/20 or $34.454744/40**, in addition to the unchanged Anthropic commitments. These are assumptions layered on Anthropic token counts, not measured GPT costs. Actual rendered requests and stage counts must replace them.

The proposed $25 and $50 ceilings allow some variation but are not guaranteed sufficient; stop rather than auto-increase them. The $5 initial OCR diagnostic is included separately, bringing the proposed maximum to $80. Keep G evaluation-only; this adds a local OpenAI evaluation credential and no Streamlit Cloud key unless production is later approved.

Dev20 alone yields an interesting diagnostic, **not a generalizable superiority claim**. A properly frozen held-out arm could support a publishable *exploratory pilot comparison* of known-reference recovery, coverage, failure modes, consistency and measured cost. It cannot establish complete alias precision, true mention/identity accuracy, universal model superiority or performance on unrepresented books. No current evidence says GPT will win. Report a negative or inconclusive result and stop; do not maintain a production provider merely because the experiment existed.

### 5. Photo ingestion and the legacy backlog

The premise needs narrowing. The revised [photo-ingestion design](photo_ingestion_design_2026-11-05.md), especially its optional-classifier and cost sections, uses **deterministic black-frame detection**, not Haiku separator classification. [batch_splitting.py](../batch_splitting.py) requires mean luminance below 5 and maximum below 18; ambiguous dark/corrupt images stop. The recommended dedicated book-camera forward path keeps the classifier **OFF**, giving **zero classifier calls and cost** in ordinary sessions.

The optional mixed/legacy book/non-book/unsure classifier estimates 192 nonseparator calls for a 200-photo example. Its planning assumptions are 361 image tokens for a 512-square thumbnail, 400 text/envelope tokens and 128 output tokens, at Haiku $1/$5 per million:

```text
192 × ((361 + 400) × 1 + 128 × 5) / 1,000,000 = $0.268992
```

The exact thumbnail billing and prompt size remain estimates. Even a free replacement could save at most **$0.269 per such session**. At those assumptions, consuming $200 would require roughly 744 sessions or 143,000 calls; no such recurring demand is established.

For OpenAI the correct estimate is `192 × ((actual_OpenAI_image_tokens + text_tokens) × input_rate + output_tokens × output_rate) / 1,000,000`. If one *hypothetically* reused 761 input/128 output counts, the unverified rates give **$0.0977664 for mini or $0.488832 for full GPT-4.1**. Those are arithmetic illustrations, not valid image-billing predictions. No evidence establishes an accuracy, privacy-error or review-time advantage that makes either a better fit. **Keep Haiku for this optional step; revisit only if an adapter already exists and measured classification errors warrant a comparison.**

Legacy matters in two senses. The historical backlog uses bounded whole-date retrieval, private local ordering/separator detection and local contact-sheet review before research admission; optional thumbnail classification needs its own consent and produces proposals only. Sending those thumbnails to OpenAI would add a different recipient, possibly including personal photos, so existing Anthropic consent must not be silently repurposed. It is a finite backlog, a poor monthly-credit workload.

Separately, the older `utilities.split_photo_batch()` code also detects black frames deterministically, then can call Anthropic `locate_cover_pages()` when none are found. That direct Messages path is not automatically converted by a new vision helper. Preserve the durable path's bounded cover fallback (at most 60 images), and do not reopen an unbounded 200-image inference path to consume credits. A local legacy classifier is another secret consumer even when it reuses the helper; it must use the permitted local secret store. Routing explicitly authorized transient thumbnails through the shared app-side helper instead avoids distributing the key to the local process. No camera/phone upload agent should receive an API key.

### 6. Targeted OCR recall / reliability

[Pipeline improvement design §5](pipeline_improvement_design_2026-11-05.md#5-reliability-and-ocrs-remaining-recall-gap) documents a measured deficit worth distinguishing from a hypothetical better vision model. Four eligible failed pages account for **153 of 234 missing reference tokens (65.4%)**: three *Grinch* pages (27, 28, 31) have provider content-filter rejections, and *Book With No Pictures* p41 exhausts the 2,048-token output limit while repeating `e`. An *All for One* cover refusal is outside the text-recovery denominator. The remaining **81 deficits have uncertain truth/scope**. Completed ordinary OCR near 0.991 and seven adjudicated genuine recoveries caution against chasing apparent errors in a noisy reference.

Thus the hypothesis is **better usable coverage on known failure modes**, not demonstrated superior recognition. A future $5-capped development diagnostic can test a frozen, compliant GPT transcription request on the existing failure cohort and existing blank, repeated-letter and recovery controls, under the same scorer. Preserve and count refusals from both providers. Do not alter prompts to evade a content refusal or route refusals automatically as transient failures; this is a declared comparison on authorized research images, not a production refusal-bypass policy.

Choose challenge cases openly as development cases. Any candidate selective reread policy must later use inference-time signals—schema failure, truncation/runaway output, unresolved text markers—not a hidden list of reference mismatches. Never choose a response by similarity to gold, silently salvage a prefix into success, or raise p41's cap and assume the repetition problem is solved. Keep preprocessing fixed and retain all attempts/costs. Generalization requires the separately frozen arm described above; four failures cannot establish an overall recall gain.

For scale, dev20 OCR alone contains 4,172,379 input and 84,552 output tokens over 747 completed calls. Fixed-token repricing gives **$9.021174 for the entire 20-book OCR workload** on the unverified full-model rate; the targeted diagnostic should be much smaller, but its $5 cap is a limit, not an estimate derived from average page size. If no useful recovery appears, stop before the full arm. This is more substantive than classification pennies, but still shares the full adapter/accounting entry cost.

### 7. Other research uses

A **small, source-cited literature digest** tied to a question Chris already needs answered is a plausible recurring option outside extraction. A text-only example of 100 tasks/month, each 10,000 input and 1,000 output tokens, costs **$2.80 full-model or $0.56 mini** at the unverified rates, excluding all search/file-search/storage/other tool charges. A proposed $10 monthly cap would require checking those current prices and enforcing request/tool limits. Coverage by the benefit does not make built-in tools unmetered. Start from public sources, retain citations for human checking, and measure time saved; do not build a document-ingestion service simply to burn credit. This needs a local key and metered helper, though it need not touch production's key path.

Speaker/addressee and character-language extraction serve the actual research, but **independent correctness labels are absent and the fresh annotation programme was cancelled**. GPT agreement or judging cannot supply those labels. A bounded hypothesis-generating prototype might be useful on already-exposed material, but would not validate downstream accuracy. New demographic/theme inference, generated illustrations and unrequested agent automation have no evidenced benefit here. Deterministic alias grouping, reference/scorer repairs and cached analysis offer stronger immediate value at $0 API spend. None of these should be relabelled as API-funded Codex work.

## Secrets, expiry controls and checks before any later spend

All OpenAI-use options introduce another credential and its rotation/revocation burden. Keep secrets only in the allowed **gitignored `.streamlit/secrets.toml` and Streamlit Cloud**; never commit `*.env`, `*secret*`, `*credential*`, `*token*` or `*api_key*`. No secret was read or changed in this design. Evaluation should use an explicitly selected, organisation-bound `EVAL_OPENAI_API_KEY` passed into `get_openai_client`, with no fallback to production secrets, environment discovery or SDK defaults. It adds a local secret consumer, not a new file/store. Production failover additionally provisions Cloud `OPENAI_API_KEY`; a separate project/key helps attribution but does **not** prove balance isolation or a hard spending cap. Do not put keys in prompts, ledgers, browser code, phones or logs.

| UNVERIFIED dependency | Exact check that would settle it |
|---|---|
| OpenAI claim/link completion, eligible account, remaining credits, cycle dates and payment-method state | Chris checks the owned target organisation's Billing/credit grant details and subscription link. Record grant/expiry and account identity without copying secrets. Confirm both providers' separate cycles. |
| Current model availability, prices, image/token/cache semantics and Python 3.10 SDK support | Official pricing, chosen model/Responses/usage and SDK compatibility documentation; dated provider-specific price manifest. A later authorized metered development pilot validates usage extraction and ledger arithmetic against account usage. |
| Actual organisation rate tier and request limits | OpenAI organisation/project Limits UI and chosen model's documented context, image, request-size and output limits. Do not infer limits from Pro's subscription tier or the credit amount. |
| Exact Anthropic exhausted-credit exception/body; OpenAI's own quota/funding errors | Official provider error documentation plus redacted confirmed error fixtures; synthetic tests derived from those facts. No forced production exhaustion. Until confirmed, surface unknown funding errors and require manual recovery instead of automatic substitution. |
| Batch API, fine-tuning and priority-processing benefit eligibility | An explicit official benefit statement covering the exact endpoint/service tier, or written OpenAI support confirmation for this organisation/benefit. Current product pricing alone cannot establish eligibility. **No option here assumes these are funded, or applies Batch discounts.** |
| Hard caps or project-level balance isolation | Actual account controls/documentation showing whether limits reject requests or merely alert. Regardless, enforce local pre-dispatch reservations across concurrent consumers; no reliance on an unverified dashboard budget. |
| Built-in tool/storage prices, if a research workflow uses them | Official current price schedule for each enabled tool and any storage retention; explicitly include these units in reservations and settlement before use. |

Before an OpenAI experiment: finish correct accounting, keep it offline from Firestore/S3 mutation, bind the intended credit organisation, verify the price manifest and limits, pass offline adapter tests, and obtain the specific experiment allocation. Before production: additionally establish quality gates, complete durable provenance and recovery paths, reserve both balances, and test the marked-draft workflow. These are implementation gates, not permission requests or work performed in this design.

## Open questions for Chris

1. **Is a provider comparison worth the maintenance now?** Options: decline for now; fund the bounded evaluation-only study; prioritize production failover. **Recommendation: decline now and finish dev20/Anthropic holdout; runner-up is the $5 → $25 → $50 gated study if the comparative research question matters.** No requirement to use the full $200.
2. **What continuity policy should govern the pilot if Anthropic funding runs out?** Options: protect a live reserve and pause/manual-complete AI work when needed; explicitly permit paid continuity; invest in validated OpenAI fallback. **Recommendation: reserve-first plus recoverable manual/paused operation now.** Size the reserve to Chris's expected remaining pilot workload; the $100 + $20 illustration is not a demand forecast.
3. **If fallback is later pursued, which outputs may enter the research corpus?** Options: exclude fallback from corpus entry; allow mandatory-provenance drafts pending existing review; allow attributed automated output under a revised analysis protocol. **Recommendation: marked drafts with existing review, preserving provider-stratified analysis.** Silent substitution should not be an option.
