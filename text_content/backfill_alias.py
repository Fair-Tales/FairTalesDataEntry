"""Messages for the offline-first alias canonical backfill CLI."""


class AliasBackfillText:
    description = (
        "Add missing alias canonical fields without changing recorded surfaces. "
        "Dry-run by default; use --snapshot for an entirely offline plan."
    )
    snapshot_help = "Local JSON snapshot; reads no live services."
    firestore_help = "Explicitly permit one bounded Firestore aliases scan."
    execute_help = "Apply the printed diff with conditional writes (Firestore source only)."
    max_aliases_help = "Maximum alias documents to process; one extra read detects overflow."
    checkpoint_help = "Local execution checkpoint; required with --execute."
    secrets_help = "Standalone secrets.toml path; only read with --firestore."
    project_help = "Firestore project; only used with --firestore."
    execute_source_error = "--execute requires --firestore and --checkpoint."
    positive_limit_error = "--max-aliases must be a positive integer."
    budget = (
        "BUDGET BEFORE INPUT: model/API inference cost $0; S3 calls 0; "
        "character reads/writes 0 (1,494 legacy characters require no rewrite); "
        "expected Firestore alias reads {expected_reads}; hard read cap {read_cap} "
        "including one overflow sentinel; conditional document writes at most "
        "{write_cap}. Daily free read allowance 50,000; this cap uses {share:.3f}%. "
        "Other application usage and remaining daily allowance are unknown."
    )
    snapshot_invalid = "Snapshot must contain an aliases list of {id, data, update_time} records."
    duplicate_id = "Duplicate alias snapshot ID: {document_id}"
    overflow = "Alias count exceeds --max-aliases={limit}; no writes attempted."
    missing_timestamp = "Execution requires snapshot update_time for aliases/{document_id}."
    malformed_checkpoint = "Checkpoint schema or contents are invalid; no writes attempted."
    checkpoint_mismatch = "Checkpoint does not match this source; no writes attempted."
    raw_changed = "Raw alias count/hash changed in the proposed projection; no writes attempted."
    illegal_updates = "Backfill attempted to change a field outside its additive canonical projection."
    failure = "BACKFILL STOPPED: {error}. Rerun a fresh bounded scan; earlier writes remain checkpointed."
    complete = (
        "BACKFILL RESULT: {mode}; planned={planned}; written={written}; "
        "flagged={flagged}; raw_entries={raw_count}; raw_hash={raw_hash}. "
        "Raw surfaces, attachments and matchability are unchanged by this projection. "
        "No verification scan was performed."
    )
    dry_run = "dry-run (no writes)"
    executed = "executed"
    missing_key = "secrets is missing required 'firestore_key'"
