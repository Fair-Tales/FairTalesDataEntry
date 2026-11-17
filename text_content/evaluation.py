"""User-facing qualifications for the second-half offline evaluation pipeline."""


class EvaluationText:
    exploratory = (
        "Exploratory pilot comparison, unadjusted for development selection. "
        "Reference agreement does not establish independent accuracy."
    )
    alias_smoke = (
        "Dev20 explicitly recorded alias recall is a smoke test: equivalence "
        "normalisation credits 6/10 across five eligible books; strict raw-surface "
        "comparison credits 4/10. Ten aliases cannot establish an improvement."
    )
    undefined = "No eligible denominator; undefined, never a passing gate."
    evidence_unavailable = "Evidence not available"
    changed_character_inputs = (
        "Character inputs changed; fixed-input retrieval not estimable"
    )
    interval_scope = "Sampling of the frozen selection; descriptive intervals are not population inference."
    monitoring_cohort = (
        "101 monitoring pages, not 101 photograph-verified wordless pages"
    )
    projection_integrity_scope = (
        "same-output raw archival projection and occurrence deduplication"
    )
    m4 = "Fixed-universe dictionary retrieval/uniqueness is diagnostic, not attribution accuracy."
    unreviewed = "Unreviewed empty-reference disagreement tokens are not confirmed hallucinations."
    precision = "Reference alias precision and F1 are diagnostic only; 48% (93/195) of books have no recorded aliases."
    finite_population = (
        "Stratified delete-one linearized ratio variance with finite population "
        "correction (1 - inclusion_probability); singleton non-census strata "
        "have unestimable sampling variance. Ordinary book bootstrap is also reported."
    )


class EvaluationErrors:
    """Actionable offline evaluator validation errors."""

    summary_duplicate_books = 'Summary requires one score per book id'
    summary_missing_strata = 'Summary requires a frozen alias stratum for every book'
    summary_partial_proof = 'Proof summary requires every frozen proof id'
    shared_draw_count = 'Shared draws must match bootstrap repeats'
    invalid_book_strata = 'book_strata must label every cohort id with a frozen alias stratum'
    changed_alias_strata = 'Alias counts disagree with sealed strata'

    comparison_overlay_required = "Comparisons require a hash-checked adjudication-v2 overlay; legacy overlays remain available for diagnostic scoring only"
    holdout_allocation = (
        "Holdout stratum has insufficient eligible books for the frozen allocation"
    )

    a_book_group_cannot_be_split_across_sampling_strata = (
        "A book group cannot be split across sampling strata"
    )
    adjudicated_cached_recovery_no_longer_matches_its_accepted_token = (
        "Adjudicated cached recovery no longer matches its accepted token count"
    )
    adjudication_convention_version_mismatch = (
        "Adjudication convention version mismatch"
    )
    adjudication_image_hash_mismatch = "Adjudication image hash mismatch"
    adjudication_import_is_restricted_to_the_frozen_exposed_dev20 = (
        "Adjudication import is restricted to the frozen exposed dev20 books"
    )
    adjudication_original_value_provenance_hash_mismatch = (
        "Adjudication original value/provenance hash mismatch"
    )
    adjudication_refers_to_unknown_book = "Adjudication refers to unknown book"
    adjudication_refers_to_unknown_page = "Adjudication refers to unknown page"
    adjudication_requires_records = "Adjudication requires records"
    adjudication_requires_reviewer_reason_and_evidence_provenance = (
        "Adjudication requires reviewer, reason and evidence provenance"
    )
    adjudication_snapshot_id_does_not_match_the_frozen_snapshot = (
        "Adjudication snapshot_id does not match the frozen snapshot"
    )
    alias_correction_requires_a_stable_unique_reference_entity_id = (
        "Alias correction requires a stable unique reference entity id"
    )
    alias_correction_requires_an_entity_id_and_surface = (
        "Alias correction requires an entity id and surface"
    )
    bibliographic_string_correction_must_be_a_string = (
        "Bibliographic string correction must be a string"
    )
    bootstrap_repeats_must_be_positive = "Bootstrap repeats must be positive"
    character_correction_must_be_entity_records = (
        "Character correction must be entity records"
    )
    conflicting_or_duplicate_adjudication_records = (
        "Conflicting or duplicate adjudication records"
    )
    credit_correction_must_be_a_list_of_names = (
        "Credit correction must be a list of names"
    )
    duplicate_book_ids_in_paired_comparison = "Duplicate book ids in paired comparison"
    effective_reference_corrections_require_verified_status = (
        "Effective reference corrections require verified status"
    )
    evidence_artifacts_are_append_only_choose_a_new_output = (
        "Evidence artifacts are append-only; choose a new output directory"
    )
    expected_adjudicated_page_is_absent_or_ambiguous = (
        "Expected adjudicated page is absent or ambiguous"
    )
    frozen_empty_empty_monitoring_cohort_must_contain_101_pages = (
        "Frozen empty/empty monitoring cohort must contain 101 pages"
    )
    invalid_adjudication_verdict_status = "Invalid adjudication verdict/status"
    metric_numerator_denominator_must_be_finite_nonnegative_counts = (
        "Metric numerator/denominator must be finite nonnegative counts"
    )
    monitoring_cohort_page_image_identity_mismatch = (
        "Monitoring cohort page/image identity mismatch"
    )
    monitoring_cohort_snapshot_identity_mismatch = (
        "Monitoring cohort snapshot identity mismatch"
    )
    original_blank_page_story_flag_mismatch = "Original blank-page story flag mismatch"
    paired_comparison_requires_identical_book_ids = (
        "Paired comparison requires identical book ids"
    )
    paired_comparison_requires_identical_eligible_reference_hashes = (
        "Paired comparison requires identical eligible reference hashes"
    )
    paired_comparison_requires_identical_sampling_metadata = (
        "Paired comparison requires identical sampling metadata"
    )
    paired_comparison_requires_identical_schema_convention_matching_normalizer_and = "Paired comparison requires identical schema, convention, matching, normalizer and overlay protocols"
    paired_comparison_requires_the_complete_named_metric_registry = (
        "Paired comparison requires the complete named metric registry"
    )
    probability_sample_requires_0_inclusion_probability_1 = (
        "Probability sample requires 0 < inclusion_probability <= 1"
    )
    publication_correction_must_retain_scoped_target = (
        "Publication correction must retain scoped target"
    )
    publication_equivalences_require_a_reviewed_versioned_event_scoped_table = (
        "Publication equivalences require a reviewed versioned event-scoped table"
    )
    publication_year_correction_must_be_a_positive_integer = (
        "Publication-year correction must be a positive integer"
    )
    reference_confirmed_verdict_cannot_change_the_original_value = (
        "Reference-confirmed verdict cannot change the original value"
    )
    resolved_adjudication_requires_corrected_value = (
        "Resolved adjudication requires corrected_value"
    )
    snapshot_prediction_identity_mismatch = "Snapshot/prediction identity mismatch"
    story_correction_must_be_boolean = "Story correction must be boolean"
    text_correction_must_be_a_string = "Text correction must be a string"
    unknown_adjudication_field = "Unknown adjudication field"
    unknown_adjudication_schema_version = "Unknown adjudication schema_version"
    unknown_metadata_adjudication_field = "Unknown metadata adjudication field"
    verified_blank_image_identity_mismatch = "Verified blank image identity mismatch"


class ScanSummaryText:
    title = 'Equal-book agreement'
    primary = 'Primary figures'
    descriptive = 'Strict and descriptive figures'
    sensitivity = 'Sensitivity: {name} ({excluded} books excluded)'
    sensitivity_names = {
        'heldout_author_shared_ids': 'shared authors',
        'heldout_alias_quoted_ids': 'quoted aliases',
    }
    labels = {
        'story_text_accuracy': 'Story-page text accuracy',
        'text_accuracy': 'Text accuracy (all pages)',
        'story_inserted_text_per_100': 'Story-page inserted tokens per hundred reference story tokens',
        'gender_found_agreement': 'Gender agreement among found characters',
        'human_found_agreement': 'Human agreement among found characters',
        'protagonist_found_agreement': 'Protagonist agreement among found characters',
        'extra_characters': 'Extra characters (unmatched predictions)',
        'duplicate_alias_count': 'Duplicate alias form count',
        'character_recall': 'Character recall (tolerant)',
        'gender': 'Gender (best-case recall)', 'human': 'Human (best-case recall)',
        'protagonist': 'Protagonist (best-case recall)',
        'strict_character_recall': 'Character recall (strict)',
        'strict_gender': 'Gender (strict)', 'strict_human': 'Human (strict)',
        'strict_protagonist': 'Protagonist (strict)',
        'alias_coverage': 'Explicit alias coverage', 'alias_recall': 'Alias recall',
        'additional_aliases': 'Additional alias forms', 'groundedness': 'Groundedness',
        'additional_groundedness': 'Additional forms groundedness',
        'duplicate_aliases': 'Duplicate forms',
        'inserted_text_per_100': 'Inserted tokens per hundred reference tokens',
        'unsuccessful_pages_per_100': 'Unsuccessful pages per hundred attempted pages',
    }
    scope = (
        'Two-sided 95% percentile bootstrap ranges: 20,000 whole-book resamples '
        'within frozen alias strata, equal weights, seed 20261110, sorted ids and '
        'linear 2.5th/97.5th percentiles. Book draws are shared across fields. '
        'These approximate sampling ranges assume comparable independent books.'
    )
    undefined = 'Undefined measures are omitted and counted; empty usable bootstrap draws are omitted.'
    perfect = 'All contributing books score 100%; the point range cannot quantify unseen errors.'
    not_applicable = 'The proof sensitivity lists do not apply to this cohort.'
    remaining = 'Sensitivities retain remaining stratum counts; no replacement books are selected.'
    line = '{label}: mean {mean}; 95% range {interval}; books {books}; items {items}; undefined books {undefined}; unknown item counts {unknown}.'
    descriptive_line = '{label}: mean {mean}; pooled {numerator}/{items}; books {books}; undefined books {undefined}; unknown item counts {unknown}.'
    count_line = '{label}: mean {mean}; total {numerator}; books {books}; undefined books {undefined}.'
    range_clause = '95% range {interval}.'
    counts = 'Cohort books: {books}; unavailable books: {unavailable}; strata: {strata}.'
    unavailable = 'Unavailable'
    no_range = 'Not estimated'
    render_scope = (
        'Stored-render development demonstration, not scan performance. Whole-book '
        'Word-reference text accuracy and inserted-token counts are unavailable '
        'from these stored scores. Character, attribute, alias and page-outcome '
        'figures use the exposed stored outputs.'
    )


class BatchText:
    help = 'Submit dry-run requests to Anthropic at batch rates, or resume collection'
    resume = 'Collect the saved batch; never submit again'
    wait = 'Poll every 60 seconds until results are ready'
    confirm = 'Type AFFIRMATIVE to allow metered paid calls up to this run cap: '
    declined = 'No paid calls authorized.'
    anthropic_only = 'Batch submission currently supports Anthropic only'
    pending = 'A batch is pending; use batch --resume instead of submitting again'
    no_id = 'No saved batch id; if submission was interrupted, reconcile with the provider before retrying'
    empty = 'No uncached requests; run extract --dry-run to discover the next round'
    too_large = 'Batch exceeds 100,000 requests or 256 MB; use smaller book selections'
    missing_reservation = 'Batch result has no reservation; reconcile the ledger'
    unknown_result = 'Unknown batch result status: {status}'
    incomplete = 'Missing batch results; reservations retained; resume collection'
    status = 'Batch {id}: {status}; {count} results collected'
    failed = '{key}: {status}; not cached, may be retried'


class TuningLoopText:
    noise_help = 'Measure per-set sample SD from unchanged scored replicates; no source reads'
    attempt_help = 'Judge a scored candidate and append an aggregate attempt row; no source reads'
    scored_help = 'Numeric tuning_scores.json produced by scan scoring'
    arm_help = 'Fresh scored runs for this arm (both sets unless a Check list is supplied)'
    check_arm_help = 'Fresh Check runs for this arm; defaults to the main arm list'
    empty_arm = 'Each arm needs at least one run per set'
    unequal_arms = 'Both arms must have the same number of runs per set'
    arm_configuration = 'Every run in an arm must use one configuration'
    fresh_runs = 'Comparison arms must be fresh: no shared runs or calibration runs'
    fresh_replicates = 'Comparison arms and calibration must use distinct replicate tokens, even in different run directories'
    missing_replicate_tokens = 'Noise floors lack replicate tokens; regenerate them with tuning-noise before comparing'
    invalid_quantile = 'Student-t requires probability in [0.5, 1) and positive integer degrees of freedom'
    repetitions = '; R={R}, r={r}, m={m}'
    omitted = 'Omitted cached, identical measures: {measures}'
    none = 'none'
    target_help = 'Measure named before running the candidate'
    log_help = 'Shared attempts.jsonl beside the tuning runs; keep using the same log'
    invalid_books = 'Tuning scores require unique ids from Iterate or Check only'
    invalid_run = 'Use numeric tuning-scores-v1 with run, configuration, replicate and measurement identities'
    invalid_metrics = 'Tuning scores require all named measures, with finite numbers or null'
    incompatible = 'Scored runs and noise floors must share measurement and frozen cohort identities'
    unpaired = 'Paired tuning requires identical books in each set'
    incomplete = 'An attempt requires every frozen Iterate and Check book; rescore both complete sets'
    no_runs = 'Supply at least one scored run; fewer than two replicates cannot measure a floor'
    configuration_changed = 'Noise replicates must use the same unchanged configuration and code'
    duplicate_replicates = 'Runs require distinct run ids and replicate identities; do not count one run twice'
    replicate_stages_changed = 'Runs and noise floors must use the same refreshed-stage set (--replicate-stages)'
    measured = 'Sample SD of replicate set means'
    no_floor = 'No floor: need at least two scored replicates on the same defined book panel'
    undefined_measure = 'No verdict: measure undefined or its contributing books changed between runs'
    invalid_target = 'Target must be a primary, alias_coverage or an automatic guardrail; cost is reported only'
    clause = 'direction-adjusted change {improvement:.6g} {operator} {threshold:.6g}: {result}'
    passed = 'pass'
    failed = 'fail'
    no_verdict = 'no verdict'
    verdict = '{verdict}: target {target}'
    clause_line = '{set} {measure} ({rule}): {reason}'
    description_required = 'An attempt requires both a hypothesis and a description of the change'
    limit = 'Tuning stopped: {accepted}/15 accepted changes; {attempts}/30 attempts; no row appended'
    incumbent = 'Baseline must use the last accepted configuration (retain it after rejection or no verdict)'
    cost = 'Reported only: any cost increase needs a stated gain in the hypothesis/change'
    logged = 'Logged attempt {attempt}; accepted changes {accepted_changes}.'
    floors_written = 'Noise floors written to {path}; {replicates} replicates. Undefined floors remain null.'
