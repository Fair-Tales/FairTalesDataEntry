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
