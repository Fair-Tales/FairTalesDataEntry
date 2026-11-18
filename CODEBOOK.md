# Fair Tales research export codebook

Prepared 16 November 2026 from repository code and recorded decisions, without
reading corpus records. See [DATASHEET.md](DATASHEET.md) for the cohorts, intended
uses and unresolved release questions. This describes the current branch's
export, not a deposited dataset or a claim that existing records have been
backfilled to the current schema.

## Reading the export

The ZIP writer in [pages/admin.py](pages/admin.py) passes raw records through
[research_export.py](research_export.py). It writes `books.csv`, `pages.csv`,
`characters.csv`, `aliases.csv`, `authors.csv`, `illustrators.csv` and
`publishers.csv` when rows exist. Characters and aliases marked `rejected`,
`superseded` or `withdrawn` are omitted. Other records are not restricted to
completed or validated books. A collection that fails to export can have an
error file in place of its CSV; an absent CSV does not establish an empty cohort.

**There is no fixed column whitelist.** Each CSV contains the union of stored
keys plus the research projection's additions. The entries below cover declared
entity fields, importer fields, known legacy fields and projected columns found
in the code. Unrecognised historical columns may also pass through; their
meaning must be resolved before use. Shared fields are documented once, with
their applicable collections stated. Nested keys are described separately;
they are not additional CSV columns unless explicitly shown with a dotted name.

CSV serialization has several material limitations:

- Null (`None` in Python), an absent field and an empty string all become an
  empty cell. They cannot be distinguished from the CSV alone. The projection
  does not generally replace legacy sentinels such as `-1`.
- Booleans are the strings `True` and `False`; numbers and timestamps are also
  stringified. Lists and maps use Python-style string representations, **not
  JSON**. Nested references are not recursively converted to identifiers.
- A top-level database reference becomes its document ID. A reference already
  stored as a string retains that string, which may include a collection path.
  The writer does **not** add the row's document ID as a column. Consequently,
  reference joins are not guaranteed to be reconstructable from these CSVs,
  especially for legacy records without `storage_id`.
- Exporting does not load entity defaults, update records, recover omitted
  evidence, recompute counts or turn an administrative status into validation.

In the tables, **blank** means the empty-cell ambiguity above; **unknown** means
the repository supplies no reliable value, not a negative finding. Empty lists
(`[]`) and maps (`{}`) mean no entries recorded, not necessarily an exhaustive
search. Allowed values describe current writers; raw historical values are not
validated by the exporter. “Person” includes manual entry and corrections;
“import” means transfer from the original study; “AI” means pipeline extraction;
“lookup” means automated bibliographic/person lookup. Software also derives
identifiers, statuses and projections deterministically; these are not human
research codes.

## Coding rules and provenance

The binding character rules are [DECISIONS 015 item 8 and 018 items 1–2](DECISIONS.md)
and [CharacterForm](text_content/forms.py). Gender follows textual cues,
including pronouns and gendered words, then clearly gendered names when no
other cue exists ([DECISIONS 028–029](DECISIONS.md)), never illustrations,
activities or stereotypes. `Non-specific` means the text gives no gender cue; `Transgender`
requires an explicit statement. Pictures may inform the AI's human/non-human
decision, not its other character codes. Protagonist means the character(s)
around whom the action revolves, including true ensembles ([DECISIONS 029–030](DECISIONS.md));
plural means a group or collection of characters. Fantasy beings are non-human, and
subjects of stories told within the book count as characters while hypothetical
figures do not ([DECISIONS 030](DECISIONS.md)). The original study coded
gender, human/non-human and protagonist, but **never coded ethnicity,
disability or plural** in the sources used by the importer.

For imported characters, those three never-coded fields are exported as null
unless there is evidence that a person set that specific current value. Explicit
per-field human provenance qualifies; otherwise the latest edit-log entry for
that entity and field must identify a person and match the current value **and type**, with no
explicit current machine provenance. Reviewing a book or editing a different
field does not qualify. A person-coded `False` or `Not specified` is retained.
Non-imported values are preserved, including old defaults. See the complete
[C3 missingness rule](planning/research_export_rules_2026-11-16.md).

Current AI character writers retain missing or invalid gender/Boolean codes as
null, rather than guessing a category. This C2 change does not repair old
records. Per-field provenance (C1) likewise does not retrospectively establish
origins for old values. C1/C2 here are implementation-plan items, distinct from
the research conventions with the same labels below.

Three research conventions remain **provisional**, not validated findings:

- **C1:** preserve book-authored lettering, including illustration lettering,
  with region labels so analytical selections can be changed.
- **C2:** `contains_story` means participation in the main work, including
  informational and wordless narrative pages; bibliographic matter, decorative
  endpapers, glossary/index and supplemental activities are excluded by default.
- **C6:** explicitly credited retellers count as authors in the analytical view,
  while the original credit roles are retained.

These statuses come from [DECISIONS 014 item 9](DECISIONS.md) and
[conventions.py](conventions.py). That module also marks entity-population,
short-form alias and claim-limit conventions provisional; a stored version
records a rule choice, not empirical confirmation.

## Shared record fields

Unless qualified, these rows apply to all seven collections. Entity-specific
fields follow in later sections.

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `is_registered` | Boolean: record registered in the app. | Software/import bookkeeping. | Blank means unrecorded; not a quality or review flag. |
| `entered_by` | Account reference or string identifying the entry account. | Software at entry/import. | Blank means unknown entrant; an import account does not identify the original coder, and a person's account does not prove human authorship of every value. |
| `datetime_created` | Record creation timestamp. | Software/import. | Blank or legacy `-1`: unrecorded; import time is not original annotation time. |
| `last_updated` | Last recorded update timestamp. | Software. | Blank or `-1`: unrecorded; not field-specific history. |
| `source` | Record-level origin string; current origins include `unknown`, `legacy_unverified`, `original_study_import`, `ai_pipeline`, `human_entry`, `human_correction`. Primarily characters/aliases; forced to `original_study_import` on imported books. | Import, AI or manual workflow. | Blank/unknown means origin unresolved. Not a guarantee that every field shares this source; not added universally. |
| `value_provenance` | Map from field name to current-value origin; principally pages, characters and aliases. See nested structure below. | Per-field import/AI/person writers; retained on any record carrying it. | Blank/`{}` or absent field entry means unknown. A book review alone does not change all origins. |
| `imported` | Boolean identifying original-study import; added to books, characters, illustrators and publishers only. | Export derives it from recorded source or importer identity. | Always supplied for these four collections. `False` means no recognised marker, not verified manual entry. |
| `book` | Owning book reference; pages, characters and aliases. | Person's current book, import mapping or AI workflow. | Blank: owner not recorded; joins have the ID limitation above. |
| `needs_review` | Boolean flag for import diagnostics; books/pages. | Import cleaning, missing-input or consistency checks. | Blank means no recorded diagnostic, not passed review. `False` is also not validation. |
| `review_note` | Free-text reason for a diagnostic; books/pages. | Automated import checks/AI. | Blank: no note; may contain source text and is not an adjudication. |

`value_provenance[field]` may contain `source` (`human`,
`original_study_import`, `ai_pipeline`, `unknown`), `account`, `timestamp`,
`model` (served model), `requested_model`, `prompt_variant`, `prompt_version`
and `prompt_sha256`. Missing call details remain missing or the literal
`unknown`; no model/date is reconstructed. `status=not_supplied` marks an
absent model code. `reviewed_by`/`reviewed_at` record review without necessarily
changing authorship. A changed AI suggestion gets human provenance with
`generated_from` retaining the producing AI origin. Account-bearing fields are
not anonymised by this export. Sources:
[corpus_provenance.py](corpus_provenance.py) and
[write-through provenance](data_structures/corpus_provenance.py).

## Books

Sources: [Book](data_structures/book.py), [BookForm](text_content/forms.py),
[import builders](scripts/import_pilot_data.py),
[bibliography projection](bibliography.py) and [DECISIONS 023](DECISIONS.md).

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `title` | Entered book title; string. | Person, original-study import or AI/lookup draft. | Blank: no recorded title. Title-derived storage keys can collide; separate editions still need distinct entry titles. |
| `isbn` | Edition identifier; current app writes check-digit-valid ISBN-13, converting ISBN-10. | Person or automated bibliographic/photo lookup. | Null/blank: not supplied; no backfill. Export preserves raw stored values without revalidating. |
| `edition` | Free-text edition description. | Person in book form. | Null/blank: unspecified; no controlled edition vocabulary or inferred edition. |
| `work_id` | UUID linking records judged to be editions of one work. | Person explicitly generates or copies it, once per book. | Null/blank: unassigned, including old books; never generated on export or inferred from title. It is not the book's storage ID. |
| `author` | Single author reference. | Person; imported spreadsheet mapping; AI/lookup draft. | Blank: unresolved. Single link loses multiple-credit detail; import sometimes selected the first of paired names/codes and flagged ambiguous combinations. See `credits` and provisional C6. |
| `illustrator` | Illustrator reference. | Person/AI for app books; automated web lookup for imports unless later human evidence exists. | Blank: unknown, not no illustrator. Original imported illustrator metadata is not independent human coding. |
| `illustrator_source` | Imported book link origin: `human`, `automated_lookup`, `missing`. | Export's field-specific evidence rule. | Not added for other books; blank there is not evidence of manual coding. Source concerns the link, not the shared illustrator name. |
| `publisher` | Export's event-scoped photographed-edition **imprint**, as a string. | Bibliography projection from retained publication evidence. | Null/blank if unresolved or not evidenced; no automatic promotion of an old publisher link. Distinct from publishing house. |
| `publisher_source` | For imports: `publication_evidence` or `missing`, following projected `publisher`. | Export projection. | Not added to other books. Evidence may be AI extracted; label does not establish human verification. |
| `legacy_publisher` | Original stored publisher value before export projection, often a reference. | Old person/AI/lookup/import workflow, preserved verbatim. | Null/blank: not recorded. Event, imprint and house may be conflated; not a verified edition fact. |
| `legacy_publisher_source` | Imported legacy link origin: `human`, `automated_lookup`, `missing`. | Export uses current-value evidence for the stored publisher field. | Not added for other books. No lookup date/model/citation is invented. |
| `published` | Legacy form's “date first published”; integer year. Form offers 1900 through current year. | Person, import spreadsheet or lookup/AI. | `-1`/blank: unknown. Legacy scope is unverified; form can preselect a year, so presence is not evidence of deliberate coding. |
| `published_year` | Projected first-publication year, at the accompanying scope/territory. | Evidence-based bibliography projection. | Null/blank if unresolved; not interchangeable with edition year or unscoped `published`. |
| `legacy_published_year` | Stored `published_year`, or `published` when that key is absent. | Export preserves pre-projection value. | Blank/`-1`: unknown; a present null does not fall back to `published`. Historical scope may be wrong. |
| `published_year_scope` | `work_first`, `territorial_first`, or `unknown`. | Projection from first-publication evidence. | Blank/unknown: scope unavailable; territorial first is not worldwide first. |
| `published_year_territory` | Free-text territory for first-publication event. | Publication evidence/projection. | Blank: unstated or not applicable; no controlled geography vocabulary. |
| `imprint` | Stored photographed-edition imprint name. | AI evidence projection or person editing publication form. | Blank: unrecorded. Raw stored field can differ from export's evidence-filtered `publisher`; inspect target status. |
| `publishing_house` | Stored publishing organisation/parent, separate from imprint. | AI evidence projection or person. | Blank: unresolved. Do not substitute it for imprint or collapse imprints to parent companies. |
| `character_count` | Stored number of characters; integer. | Import counts references; app bookkeeping where supplied. | `-1`/blank: uncomputed. Export does not recount after omissions/edits. |
| `page_count` | Stored/declared number of pages; integer. | Person/upload/import. | `-1`/blank: unknown; may differ from registered page documents and is not necessarily printed pagination. |
| `word_count` | Stored word count; integer. | Software where calculated; importer sets `-1`. | `-1`/blank: uncomputed. No uniform tokenizer or up-to-date count is guaranteed by export. |
| `sentence_count` | Stored sentence count; integer. | Software where calculated; importer sets `-1`. | `-1`/blank: uncomputed; segmentation/count freshness is not established here. |
| `first_content_page` | First page in recorded main-content range; integer. | Person or original-study range import. | `-1`/blank: range unknown; legacy conventions may differ from provisional C2. |
| `last_content_page` | Last page in recorded main-content range; integer. | Person or original-study range import. | `-1`/blank: unknown; does not verify completeness of intermediate pages. |
| `characters` | List of character references linked to book. | Person/AI workflow or import. | Blank/`[]`: none recorded, possibly legacy absence; older records can contain a numeric count instead. No guaranteed synchronisation with exported character rows. |
| `entry_status` | Workflow string; writers include `started`, `complete`, `completed`. | App/import workflow. | Blank: unknown. Both completion spellings exist; completion does not imply human validation or complete pages. Raw other statuses pass through. |
| `validated` | Boolean book validation flag. | Person workflow; export corrects imported flags. | Imported flag is true only with a recorded person validator; otherwise false. Non-imported flags remain raw. Neither is an accuracy measurement. |
| `validated_by` | Validator account reference/string. | Workflow/import, then export correction. | Cleared on imports lacking valid person validation; blank means no qualifying validator recorded. Not pseudonymised. |
| `validated_by_person` | Boolean: stored `validated` is exactly true and validator is a recognised person rather than a known machine/unknown account. | Export, for every book. | Always supplied. Account classification is a software rule, not independent verification of the review. |
| `datetime_submitted` | Submission timestamp. | Workflow/import. | Blank/`-1`: absent; importer submission time is not a person's review time. |
| `validation_active_at` | Last validation-session heartbeat timestamp. | Software. | `-1`/blank: no heartbeat recorded; temporary workflow state. |
| `validation_active_by` | Account holding validation session. | Software. | Blank: none recorded; neither final validator nor research annotation. |
| `validation_active_token` | Opaque validation-session ownership token. | Software. | Blank: none; operational data, not a corpus identifier. |
| `photos_uploaded` | Boolean recorded availability of page images. | Upload/import workflow. | Blank: unknown; true is not a fresh availability or completeness check. |
| `photos_url` | Stored image-folder location; string. | Upload/import software. | Blank: location absent; not a redistribution permission. Imported images may be Word-page renders. |
| `comment` | Free-text comment about the book. | Person, sometimes automated metadata workflow. | Blank: no comment; not a controlled research code. May contain identifying or copyrighted material. |
| `review_pages` | List of page numbers flagged during import. | Import diagnostics. | Blank/`[]`: no recorded page flags, not all pages verified. |
| `high_priority_review` | Boolean summary of urgent import diagnostics. | Import checks/AI cleaning. | Blank: unknown; false is not proof of correctness. |
| `disability` | Boolean theme: disability explicitly represented in the book. | Person's theme selection. | Original study supplied no theme coding; absent/blank is not false. App defaults false, which may mean unselected/unreviewed. |
| `race_ethnicity` | Boolean theme: race/ethnicity explicitly represented. | Person's theme selection. | Same never-coded/import and default-false limitation as `disability`. |
| `sexuality` | Boolean theme: sexuality explicitly represented. | Person's theme selection. | Same never-coded/import and default-false limitation. |
| `religion_spirituality` | Boolean theme: religion/spirituality explicitly represented. | Person's theme selection. | Same never-coded/import and default-false limitation. |
| `gender` | Boolean theme: gender explicitly represented; not a character/author gender category. | Person's theme selection. | Same never-coded/import and default-false limitation. |
| `social_class` | Boolean theme: social class explicitly represented. | Person's theme selection. | Same never-coded/import and default-false limitation. |
| `age` | Boolean theme: age explicitly represented. | Person's theme selection. | Same never-coded/import and default-false limitation. |

Theme selection concerns presence, not approval of how a theme is represented.
The importer intentionally omitted all seven theme fields. Saving later app
defaults can introduce false values without establishing that a person assessed
each theme; the C3 masking rule does not cover themes.

### Publication evidence and projected book columns

[DECISIONS 014 item 8](DECISIONS.md) settles the separation of imprint and
publishing house. [bibliography.py](bibliography.py) additionally separates the
photographed edition from first publication, retaining whole events rather than
assembling publisher and year from different events. Supported original/work-first
events take precedence over territorial-first events. Conflicts can leave a
target unresolved. Old unscoped metadata is retained as legacy evidence, not
silently promoted. A person's form correction is marked as a manual override
but remains unresolved without event evidence.

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `publication_export_version` | Projection version; currently `bibliography-export-v2`. | Export software. | Always added; describes column meanings, not data quality. |
| `metadata_schema_version` | Stored bibliographic schema version, currently `bibliography-v2`. | Metadata writer/person form. | Blank: legacy schema; export adaptation does not automatically add every stored-schema field. |
| `publication_policy_version` | Publication-event selection rule, currently `publication-events-v1`. | Software. | Blank: old/unknown rule; not evidence of manual review. |
| `photographed_edition` | Map for edition in the supplied photographs: publisher/imprint, house, year, territory, authority ID, evidence and field statuses. | AI extraction, deterministic reconciliation, possible person overrides. | Blank: no stored target. Member nulls/statuses distinguish unresolved claims; flattened columns may be derived even when this raw map is absent. |
| `first_publication` | Map for first-publication event, with the same members plus scope. | AI/evidence projection/person overrides. | Blank: no stored target. Territorial and work-first scopes must remain distinct. |
| `publication_events` | List of retained event statements and their evidence. | AI extraction or retained older evidence. | Blank/`[]`: none recorded. Statements are candidates; not all qualify for projection. |
| `legacy_publication` | Map preserving old publisher/year, source, record/source IDs, timestamps, record hash and migration status. | Software preserves earlier record. | Blank: no retained bundle; `needs_review` is not a verified publication claim. Can contain account references. |
| `credits` | List of contributor names, explicit roles and evidence. | AI extraction/reconciliation. | Blank/`[]`: no retained credits, not no contributors. Roles such as author, reteller and illustrator remain distinct; C6 author projection is provisional. |
| `metadata_conflicts` | List of competing bibliographic claims and reasons/resolutions. | AI and deterministic reconciliation. | Blank/`[]`: no recorded conflicts; does not establish correctness. |
| `metadata_conventions` | Map of convention version and per-convention rule/status. | Software, from `conventions.py`. | Blank: conventions unrecorded; provisional does not mean ratified. |
| `metadata_evidence_payloads` | List of retained joint metadata responses. | AI and extraction software. | Blank/`[]`: evidence not retained. May contain verbatim book text; not necessarily independently checked. |
| `metadata_history` | List of earlier target values and correction times/origins. | Software when a person changes publication targets. | Blank/`[]`: no recorded history; cannot infer no prior changes. |
| `metadata_reconciliation` | Map of later cross-page metadata reconciliation output. | Automated workflow. | Blank: absent/not run; raw suggestions are not necessarily accepted form changes. |
| `metadata_page_numbers` | List of source page numbers used for metadata extraction. | Software selection. | Blank: no record; does not show all potentially informative pages. |
| `metadata_prompt_manifest` | Map describing metadata prompt/version/hash. | Software. | Blank: unknown prompt; a manifest does not prove output correctness. |
| `metadata_extraction_status` | Current writer: `completed` or `invalid_response`. | AI response parsing. | Blank: unrecorded; completed means structurally usable response, not verified bibliography. |
| `photographed_edition.publisher` | Flattened edition publisher/imprint string. | Export's adapted target. | Blank: unknown; inspect target status, since flattened fields can retain unresolved manual values. |
| `photographed_edition.imprint` | Flattened edition imprint string. | Export's adapted target. | Blank: unknown; same status qualification. |
| `photographed_edition.publishing_house` | Flattened edition publishing-house string. | Export's adapted target. | Blank: unknown; not interchangeable with imprint. |
| `photographed_edition.year` | Flattened edition year; integer. | Export's adapted target. | Blank: unresolved/unstated; not the first-publication year. |
| `photographed_edition.territory` | Flattened edition territory; string. | Export's adapted target. | Blank: unstated; no standard geography coding. |
| `photographed_edition.scope` | Flattened edition scope if stored. | Export copies target member. | Normally blank: standard edition target has no `scope` member. Do not manufacture a missing category. |
| `first_publication.publisher` | Flattened imprint of selected first-publication event. | Export's adapted target. | Blank: unknown; may differ from photographed edition. Check field status. |
| `first_publication.imprint` | Flattened first-publication imprint. | Export's adapted target. | Blank: unknown; check field status. |
| `first_publication.publishing_house` | Flattened first-publication house. | Export's adapted target. | Blank: unknown; not a substitute for imprint. |
| `first_publication.year` | Flattened first-publication year; integer. | Export's adapted target. | Blank: unknown; check status, scope and territory before comparing years. |
| `first_publication.territory` | Territory of selected first-publication event. | Export's adapted target. | Blank: unstated/not applicable; not automatically worldwide. |
| `first_publication.scope` | `work_first`, `territorial_first`, `unknown`. | Export's adapted target. | Unknown/blank: scope not established. |

Within publication targets, `publisher_status`, `imprint_status`,
`publishing_house_status` and `year_status` are currently `evidenced`,
`unresolved` or `conflicting`; evidence can be machine extracted.
`manual_overrides` identifies manually changed members. `publisher_authority_id`
is optional, not a universally supplied authority identifier. Evidence entries
can carry `field`, `page_number`, `source_page_id`, `image_hash`, `quote`,
`provenance` and `plain_print`. Event records also carry scope, publisher role,
territory and support flags. A supported event requires source-linked quoted
photo evidence with an image hash, or a frozen lookup with verified event
identity. These maps are not flattened recursively by the CSV writer.

## Pages

Sources: [Page](data_structures/page.py), [corpus provenance](corpus_provenance.py),
[importer](scripts/import_pilot_data.py), [conventions](conventions.py) and
[DECISIONS 017 item 4](DECISIONS.md).

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `page_number` | Integer position in uploaded/rendered page sequence. | Upload/import software. | `-1`/blank: unassigned. Not necessarily printed page number; scans and Word exports can group pages differently. |
| `text` | Stored page transcription; string. | Original study: people entered Word documents, then import extracted/cleaned or OCR'd them. App pilot: AI draft reviewed by a person. Current app: AI or person, with per-field origin where available. | Blank may be wordless, failed, skipped, unentered or missing. Never classify wordlessness from blank alone. Import cleaning changed some human reference text; historical pilot edits are not reconstructable. |
| `contains_story` | Boolean participation in main work under provisional C2. | Original-study page-range import; AI classification or person correction. | Blank: unknown; older false/defaults may include failures. Wordless narrative can be true. Old page ranges need not implement today's convention. |
| `extraction_status` | `unknown`, `succeeded`, `failed`, `human_entered`. | Current extraction/manual-save workflow. | Blank/unknown: outcome unrecorded. Success permits genuinely empty text; human-entered may mean an edit of a prior AI draft. |
| `extraction_failure_kind` | `provider_error`, `output_blocked`, `refusal`, `truncated`, `invalid_response`, `outcome_unknown`, `missing_input`, `request_limit`. | Software classifies extraction failure. | Null/blank: no recorded failure kind, not necessarily success; read status too. |
| `extraction_evidence` | Map of retained call details, regions, page classification and versioned projections, where supplied. | AI and software. | Blank: no retained details; does not invalidate separately stored manual text. May retain old model text after edits. |
| `corrected` | Boolean: corrected image was written for this page. | Crop/rotation software or manual image edit. | Null/blank: unknown, especially legacy pages; correction is not verification of text. |
| `rotation_uncertain` | Boolean: automatic orientation check abstained/failed. | Image-processing software; manual correction can clear it. | Blank: not recorded; old default false is not evidence that an orientation check succeeded. |
| `text_source` | Import route: `layer`, `ocr`, `skipped_wordless`, `none`. | Importer. | Blank: route unrecorded. `layer` can be human Word text subsequently AI cleaned; `skipped_wordless` is an automated judgement known to have missed text. |
| `clean_status` | Import cleaning: `cleaned`, `unchanged`, `rejected`, `failed`, or empty string. | AI cleaning and importer. | Blank: no recorded cleaning outcome. Rejected/failed does not mean the stored page is verified or wordless. |
| `ocr_model` | Import OCR model identifier; string. | Importer records model used. | Blank: no model recorded/not used; not a general current-text provenance field. |
| `clean_model` | Import cleaning model identifier; string. | Importer. | Blank: no cleaning model recorded/not used; no retrospective inference. |
| `continuity` | Map containing neighbouring-page continuity judgement used in OCR skipping. | AI during import. | Null/blank: no verdict. Model confidence and fluent explanations did not establish wordlessness. |
| `review_priority` | Import diagnostic priority: `high`, `normal`, or empty string. | Automated coherence/context checks. | Blank: no priority recorded; not a person's judgement. |

Page evidence can retain text regions with kinds `narrative`, `informational`,
`illustration_lettering` and `paratext`, page-type labels, `analysis_text`,
projection status and convention versions. The provisional main-text projection
includes the first three kinds; missing/malformed regions produce a
`legacy_unclassified` fallback, not a claim that the old text was separated
correctly. These sidecars must not be mistaken for a person's current `text`.

## Characters

Sources: [Character](data_structures/character.py),
[CharacterForm](text_content/forms.py), [importer](scripts/import_pilot_data.py),
[C3 export rules](planning/research_export_rules_2026-11-16.md) and
[DECISIONS 014, 015 and 018](DECISIONS.md).

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `name` | Primary recorded character name or display label; string. | Original human coding imported; person entry/correction; AI detection. | Blank: unnamed/unrecorded. Human names can be shortened or editorial labels, not exact textual mentions; names alone do not establish identity. |
| `gender` | `Female`, `Male`, `Non-specific`, `Transgender`; textual rule above. | Person, imported original codes, or AI. Import maps F/M/NGS to Female/Male/Non-specific. | Null/blank: absent/invalid/unrecorded; distinct from coded Non-specific. Import also mapped unexpected codes to Non-specific. Original source has no Transgender observations, so agreement cannot validate that category. |
| `human` | Boolean: human versus animal, monster or other non-human; fantasy beings are non-human ([DECISIONS 030](DECISIONS.md)). Pictures may inform this decision. | Original human H/NH codes, person or AI. | Null/blank: missing/invalid current model code or unrecorded. Import mapped two anomalous source-code values to true; export does not repair them. Historical/manual default true may be unassessed. |
| `protagonist` | Boolean: main character. | Original study's per-book protagonist code, person or AI. | Null/blank: missing/invalid/unrecorded. False is a negative code when supported; old/manual default false alone does not prove assessment. Definition: [DECISIONS 029–030](DECISIONS.md). |
| `plural` | Boolean: group/collection rather than individual. | Person or AI; original study never coded it. | Imported default false becomes null unless current human evidence exists. Elsewhere null/blank is unknown and old default false can survive. Do not equate missing with singular. |
| `ethnicity` | `Not specified`, `Asian`, `Black / African`, `Hispanic / Latino`, `Middle Eastern`, `Native / Indigenous`, `White / European`, `Mixed / Other`. Apparent representation in text or illustrations according to manual form. | Person; original study never coded it. Current character AI assignment does not establish this code. | Imported defaults become null without current human evidence. Explicit human Not specified is a code, not never-coded missingness. Vocabulary is explicitly a placeholder pending team confirmation; app defaults may otherwise survive. |
| `disability` | `Not specified`, `Physical disability`, `Sensory disability`, `Cognitive / Learning disability`, `Mental health condition`, `Chronic illness`, `No disability`. Representation in text according to form. | Person; original study never coded it. Current character AI assignment does not establish this code. | Imported defaults become null without current human evidence. No disability, Not specified and missing are different. Vocabulary is explicitly provisional; non-import defaults may survive. |
| `ethnicity_source` | For imported characters: `human` or `missing`. | Export evidence rule. | Not added for other characters; missing means no qualifying current human code. |
| `disability_source` | For imported characters: `human` or `missing`. | Export evidence rule. | Same limitation; cannot infer origin for non-imports from absence. |
| `plural_source` | For imported characters: `human` or `missing`. | Export evidence rule. | Same limitation; a human false is retained. |
| `storage_id` | Stored document key; string. | Software, retaining identity across renames. | Blank: legacy key not persisted. Export does not insert missing document IDs. |
| `entity_id` | Analytical identity identifier; string. | Identity software. | Blank: absent in older records; not a globally verified cross-book identity. |
| `entity_kind` | `individual`, `collective`, `generic_group`, `discourse_role`, or legacy/default `unclassified`. | AI identity evidence/manual coding where supplied. | Blank/unclassified: unresolved, not implicitly individual. These strata were not supplied as original-study codes; population convention remains provisional. |
| `identity_evidence` | List of evidence supporting identity distinctions. | AI/identity workflow. | Blank/`[]`: none retained; does not establish an unambiguous identity. |
| `member_of` | List of group identity links. | AI/identity workflow or person correction. | Blank/`[]`: no membership recorded, not proof of non-membership. A group mention does not thereby refer to each individual. |
| `former_ids` | List of earlier identity IDs. | Identity software. | Blank/`[]`: no recorded mapping; not a complete historical crosswalk. |
| `unresolved_identity_alternatives` | List of alternative identity interpretations. | AI/identity workflow. | Blank/`[]`: no recorded alternatives, not proof all ambiguities resolved. |
| `name_is_label` | Boolean: primary name is an editorial/display label rather than an attested lexical form. | Detection/identity workflow. | Blank: unrecorded; legacy false may be a default. |
| `mentions` | List of mention evidence records. | AI/mention processing. | Blank/`[]`: none retained; not zero occurrences. Attribution accuracy is not established by dictionary matching. |
| `editorial_labels` | List of descriptive labels kept apart from textual aliases. | AI/identity workflow. | Blank/`[]`: none recorded; labels need not appear verbatim in book. |
| `raw_alias_entries` | List of original alias suggestions/evidence. | AI/identity workflow. | Blank/`[]`: not retained; not necessarily identical to accepted exported alias rows. |
| `detection_audit` | List of detection decisions/evidence. | AI/software. | Blank/`[]`: no audit retained; not human adjudication. |
| `unresolved_mentions` | List of mentions without resolved owner. | AI/identity processing. | Blank/`[]`: none recorded; not evidence all mentions found. |
| `name_history` | List of former display-name strings. | Software on person rename. | Blank/`[]`: history absent; does not imply name never changed before history tracking. |
| `entry_status` | Identity/review status string; default `unverified`. | Person/AI workflow. | Blank: legacy unknown. `rejected`, `superseded`, `withdrawn` rows are excluded from this export; other statuses pass through. Presence is not verification. |

New identity/evidence sidecars are not original-study annotations. They may be
absent even on a record with genuine original human gender, human and protagonist
codes. Do not infer that the latest schema was used by the original coders.

## Aliases

An alias is another name or descriptive referring expression for a character.
Proper names and descriptions are in scope; pronouns are coreference evidence,
body parts are not aliases of their owners, and editorial labels are separate.
The recorded alias lists are sparse and are not exhaustive negative evidence.

[DECISIONS 014 items 2–6 and 018 item 3](DECISIONS.md) require retaining raw
surfaces, grouping duplicates rather than deleting evidence, and preserving
possessors. Articles `a`, `an`, `the` may be optional during mention matching;
possessives are not. [alias_schema.py](alias_schema.py) derives a separate
canonical form using Unicode normalisation, case folding, apostrophe
normalisation and word tokenisation. It keeps articles, owners and modifiers.
Canonical equality is not an identity decision or a complete mention-matching
rule. A person can override the canonical form without replacing the raw name.

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `name` | Exact recorded alias surface; string. | Imported human coding, person or AI. | Blank: no surface. Some old comma-separated paths split punctuation and cannot recover the lost original structure. Original lists omit many legitimate forms. |
| `character` | Recorded owner reference. | Original-study import, person or AI. | Blank: owner unresolved. A shared surface can refer to different characters; do not join by name alone. |
| `effective_character` | Optional corrected owner reference used by analytical readers. | Correction workflow/person. | Blank: no override; readers fall back to recorded owner. Export does not replace `character` automatically. |
| `canonical_form` | Normalised lexical form, or explicit manual override; string. | Deterministic derivation or person. | Blank: older/unprojected record. Export does not call the reader's fallback or backfill it. |
| `canonical_source` | `derived`, `manual`; reader fallback can report `legacy_fallback`. | Canonical software/person override. | Blank: source unrecorded. Fallback is not automatically materialised by export. |
| `canonical_override` | Boolean: canonical form explicitly overridden. | Person action/software. | Blank: old metadata absent; false means derived only when bundle is valid. |
| `canonical_rule_version` | Derivation version; current `alias-canonical-v1`. | Software. | Blank: unknown rule; stale versions require careful interpretation. |
| `canonical_input_hash` | SHA-256 of exact raw surface bytes. | Software. | Blank: no recorded input hash; mismatch means bundle is stale, not that raw surface should be deleted. |
| `alias_schema_version` | Current `alias-mentions-v2`, or historical version string. | Software. | Blank: legacy record; not proof of migration. |
| `storage_id` | Persisted alias document key. Current IDs hash book, owner and exact surface together; older keys used book/name. | Software. | Blank: not persisted; raw export cannot infer ID. Legacy collisions and duplicate rows can remain. |
| `provenance` | List of alias lineage/source evidence, separate from per-value origin. | Import/AI/correction workflow. | Blank/`[]`: no retained lineage; source details are heterogeneous. |
| `entry_status` | Review/analysis status string; default `unverified`; correction writes `accepted`; may include `editorial_label`. | Person/AI workflow. | Blank: legacy unknown. Rejected/superseded/withdrawn rows are filtered; other rows, including labels, pass through. |
| `supersedes` | ID of earlier raw alias record replaced by this correction. | Correction software/person action. | Null/blank: no predecessor recorded; predecessor may be filtered out of this export. |
| `superseded_by` | ID of replacement record. | Correction workflow. | Null/blank: no successor recorded; superseded rows are normally omitted. |
| `raw_history` | List retaining earlier raw surface/ownership data. | Correction software. | Blank/`[]`: no recorded history, not proof no earlier edits. |
| `canonical_history` | List of prior canonical bundles. | Canonical-edit software. | Blank/`[]`: no recorded changes; not a complete pre-tracking history. |

## Authors

Sources: [Author](data_structures/author.py), [AuthorForm](text_content/forms.py),
[importer mapping](scripts/import_pilot_data.py) and [DECISIONS 002](DECISIONS.md).
The shared bookkeeping fields apply. The export adds no `imported` or
`name_source` column to authors.

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `forename` | Given/first name(s); string. | Person, AI/lookup draft or imported author spreadsheet. | Blank: absent/unknown; name splitting may misrepresent joint credits. |
| `surname` | Family/last name; string. | Person, AI/lookup draft or import. | Blank: absent/unknown; single-name people and multi-author strings need care. |
| `gender` | `Woman`, `Man`, `Non-binary`, `Other`, `Unknown`. This is a separate vocabulary from character gender. | Person or automated person lookup; import maps F/M to Woman/Man, otherwise Unknown. | Blank: unrecorded; Unknown is explicit indeterminacy. Lookup is not self-identification; original evidence for how author gender was established is not settled by this repository. |
| `birth_year` | Legacy optional integer year; no longer in current form/schema but can pass through. | Older person/lookup workflow. | Blank: unknown. Old default `1970` is indistinguishable from a genuine year and was deliberately not migrated (Decision 002). |

The author's computed display `name` is not a field added by this exporter;
researchers should not assume every CSV has such a column. Provisional reteller
inclusion is recorded in bibliographic credits/projections, not guaranteed by
the book's single author reference.

## Illustrators

Source: [Illustrator](data_structures/illustrator.py) and importer. Shared fields
apply, including `imported`. The original-study source used here did not supply
the imported illustrator metadata; it was filled by automated web lookup.

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `name` | Illustrator's full recorded name; string. | Person or AI/photo/lookup workflow; import lookup. | Blank: unknown, not no illustrator. Shared record may have been edited independently of a book's link. |
| `name_source` | Imported rows only: `human`, `automated_lookup`, `missing`. | Export's current-field evidence rule. | Not supplied for non-imports. Does not establish the source of each book-to-illustrator link. |
| `forename` | Legacy given-name string. | Former person/lookup form. | Blank: not supplied/obsolete schema. Current form uses `name`; export does not consolidate them. |
| `surname` | Legacy surname string. | Former person/lookup form. | Blank: not supplied/obsolete schema; same consolidation limitation. |
| `gender` | Legacy illustrator gender category from the former person form. | Former person/lookup workflow. | Blank: unrecorded/no longer collected. Historical vocabulary and evidence are not established by the current schema; do not assume character gender rules apply. |

## Publishers

Source: [Publisher](data_structures/publisher.py), importer and
[C3 export rules](planning/research_export_rules_2026-11-16.md). Shared fields
apply, including `imported`. The original-study source did not supply the
imported publisher metadata; it came from automated web lookup.

| Field | Meaning and allowed values | Coding/source | Missingness and known defects |
|---|---|---|---|
| `name` | Recorded publisher organisation/label; string. | Person, AI/photo/lookup workflow; import lookup. | Blank: unknown. Legacy entity names do not distinguish imprint from house or identify a publication event. Use the separate book-level imprint/house targets for that distinction. |
| `name_source` | Imported rows only: `human`, `automated_lookup`, `missing`. | Export's current-field evidence rule. | Not added for non-imports; source concerns this shared name, not every book's publisher link. |

Unresolved technical evidence includes the complete set of historical
pass-through fields, older illustrator gender coding and the basis of original
author gender codes. These remain open documentation questions, not invented
values or coding rules. Release questions are collected in
[DATASHEET.md](DATASHEET.md).

## Settled questions

- **Ethnicity and disability categories stay provisional** (Chris, 16 November
  2026). The menus keep their placeholder vocabularies and the original study
  never coded either field, so neither is used in the current validation. Settle
  the vocabulary before anyone analyses these fields; any revision then needs an
  explicit rule for reading older codes, never silent recoding.
