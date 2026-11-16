# Fair Tales corpus datasheet

Prepared 16 November 2026 from repository documentation and code. Counts below
are historical observations recorded in those documents, not a new database
census. This describes a developing corpus; it does not announce a release.

## Motivation

Fair Tales supports research on representation and language in children's
books, including character gender, and aims to produce a reusable research
corpus. The app records books, page text, characters, aliases and bibliographic
information. [Review §5.7](planning/fable_review_2026-11-10.md) identified the
missing documentation and unresolved release decisions; these documents address
[implementation-plan item C5](planning/implementation_plan_2026-11-11.md).

## Composition

The **4 November 2026 census** recorded **276 book records: 200 imported from the
original study and 76 entered through other app workflows**, as reported in
[the pipeline design's cohort accounting](planning/pipeline_improvement_design_2026-11-05.md).
The **6 November accounting** underlying [DECISIONS 015](DECISIONS.md) split the
76 into 59 completed/text-bearing and 17 started; excluding two automated books
left 57 completed books entered through personal accounts and 17 started books,
with 734 story pages in the completed subset (32 empty, 702 non-empty; see
[cohort accounting](planning/autonomy_validation_design_2026-11-06.md)).
These are record/work counts under the historical naming scheme, not a verified
count of distinct editions or a representative sample of children's literature;
optional work/edition identifiers were added later ([DECISIONS 023](DECISIONS.md)).

## Collection

For the original study, people entered page text in Word documents from scans
in 2021–22, and supplied character names, aliases, gender, human/non-human and
protagonist codes; ethnicity, disability and plural were not coded in the
imported sources. For the app-entered pilot, page text was **drafted by AI and
reviewed by a person**, with character suggestions also offered by AI; the
historical record does not show all reviewer changes, so these are not
independent human transcriptions ([DECISIONS 017 item 4](DECISIONS.md), correcting
015). Imported illustrator/publisher metadata came from automated lookup rather
than original human coding; current field origins and their gaps are described
in [CODEBOOK.md](CODEBOOK.md).

## Preprocessing and validation

Import extracted text from the original Word-export PDFs, sometimes used OCR
and wordless-page skipping, and applied AI cleaning; stored imported images
were renders of Word pages, not original colour book pages
([DECISIONS 017](DECISIONS.md)). The [analysis plan](planning/analysis_plan_2026-11-10.md)
records cleaning on 2,652 of 4,069 text-layer pages and requires original colour
scans and uncleaned human references for evaluation; current exports apply
[field-specific missingness corrections](planning/research_export_rules_2026-11-16.md)
and retain available provenance without repairing every historical default.
**The proof has not been run yet:** its planned five primary measures concern
text, character recovery, gender, human/non-human and protagonist agreement on
80 held-out original-study books, with a separate 40-book rehearsal; planned
results and projected precision are not observations. Agreement is not absolute
accuracy, pilot AI drafts and human reviews can share errors, and the plan's
ranges assume comparable independent books; later amendments and exposure
limitations must be read in [DECISIONS](DECISIONS.md), not inferred from cohort
size alone.

## Uses

The corpus can support exploratory analyses of recorded representation,
character-associated language and extraction agreement, with provenance and
missingness reported separately by cohort. It does not establish exhaustive
character or alias inventories, independent pilot transcription truth,
inter-annotator agreement, or performance on future books; do not pool the
cohorts as if their entry procedures were interchangeable
([DECISIONS 014–018](DECISIONS.md), [analysis plan](planning/analysis_plan_2026-11-10.md)).
The lettering, main-content and credited-reteller conventions remain provisional;
the [codebook](CODEBOOK.md) states the coding rules, unsupported categories and
CSV limitations that constrain reuse.

## Distribution

**None of the deposit scope, licence, contributor-consent or account-name
pseudonymisation decisions below has been made.** Following
[review §5.7](planning/fable_review_2026-11-10.md), these are questions for the
university's copyright and research-data services and Chris, not proposed
permissions:

- What may be deposited: derived annotations and identifiers, rather than page
  photographs or full text of in-copyright books? Which evidence excerpts or
  nested fields, if any, may accompany those annotations?
- Under what licence may the permitted material be distributed, and do
  different components require different terms?
- What consent is needed from the people who entered or reviewed records for
  their contributions and associated metadata to be deposited and reused?
- Must account names and account-bearing provenance be pseudonymised, and what
  attribution, if any, may be retained?

## Maintenance

The repository records coding and identity decisions in [DECISIONS.md](DECISIONS.md)
and implementation work in [the plan](planning/implementation_plan_2026-11-11.md);
current provenance and version fields make some changes traceable, but old
records often lack them. This documentation should be read with the code
revision that produced an export; no release version, maintenance schedule,
deposition service or public correction process is established here.

## Open questions for Chris

The distribution questions above remain for Chris and the university services;
the [codebook](CODEBOOK.md) also asks whether the placeholder ethnicity and
disability categories should be settled before research use. No additional
decision is needed to document the present corpus.
