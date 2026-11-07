"""Offline tuning exclusion messages."""


class TuningErrors:
    invalid_freeze = "Missing or malformed tuning freeze: {detail}"
    changed_exclusions = "Tuning exclusion file differs from its sealed SHA-256"
    inconsistent_freeze = "Tuning freeze ID lists or imported80 hash are inconsistent"
    invalid_lock = "Paired experiment lock needs run_id, locked_at, results_sha256 and configuration_sha256"
    changed_run = "Tuning freeze or paired experiment lock changed; use a new run directory"
    missing_record = "Missing tuning run record; prior exposure is unresolved. Use a new run directory"
    refused = "Tuning load refused: {source_ids} ({reason})"
    breach = "Tuning exposure detected: {ids}. Proof status invalidated; stop, do not replace or redraw"
    missing_source = "Source work ID is absent or unresolvable"
    excluded = "Source is excluded ({group})"
    invalid_source_file = "Malformed source identity in {path}"
    mixed_modes = "An existing tuning run cannot be opened as the original paired experiment"
    selection_absent = "Selected tuning source is absent from the loaded snapshot"
    trailing_json = "Trailing JSON content: {path}"
    malformed_json = "Malformed JSON at byte {offset}: {path}"
    missing_colon = "Missing JSON colon at byte {offset}: {path}"
    missing_separator = "Missing JSON separator at byte {offset}: {path}"
    duplicate_member = "Duplicate JSON member {key!r}: {path}"
    expected_object = "Expected a JSON object"
    expected_array = "Expected a JSON array"


class TuningHelp:
    freeze_dir = "Sealed tuning exclusion directory (default: %(default)s)"
    original = "Original paired experiment only: retain its original loading rules, outside new tuning"
    lock = "JSON record of the original paired experiment's locked results and configuration"
    audit = "Recheck a tuning run's loaded IDs before final policy lock; no content or service access"
