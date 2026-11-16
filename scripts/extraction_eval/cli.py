"""Local evaluation operations; inventory and score have no service dependencies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sqlite3
import sys
import tempfile

from .evidence import evidence_dir
from .store import atomic_json, read_json, run_lock
from .tuning import DEFAULT_FREEZE_DIR, TuningLoader, TuningRefused, TuningExposure
from text_content.tuning import TuningErrors, TuningHelp
from text_content.pipeline_runtime import (
    CYCLE_CAP_HELP, HEADROOM_HELP, HEADROOM_LINE, HEADROOM_UNCONFIGURED, RUN_BUDGET, CYCLE_UNCONFIGURED,
)

PACKAGE = Path(__file__).resolve().parent


def build_parser():
    from .scan_inputs import DEFAULT_SCAN_ROOT
    from .reference_inputs import DEFAULT_REFERENCE_ROOT
    parser = argparse.ArgumentParser(description="Frozen pilot reference-agreement harness (snapshot / extract / score)")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="Read-only Firestore/S3 export; never performs inference")
    snapshot.add_argument("--out", type=Path, required=True)
    snapshot.add_argument("--entered-by", default="pilot_import")
    snapshot.add_argument("--books", nargs="+", help="Optional exact book IDs or titles")
    snapshot.add_argument("--secrets", default=".streamlit/secrets.toml", help="Use IAM-enforced read-only credentials")
    snapshot.add_argument("--project", default="sawdataentry")
    snapshot.add_argument("--audit-source-changes", action="store_true", help="Opt in to re-reading captured source documents to detect changes")
    snapshot.add_argument("--audit-membership", action="store_true", help="Opt in to re-querying related-document membership")
    snapshot.add_argument("--audit", action="store_true", help="Enable both optional audits (additional billed reads)")
    snapshot.add_argument("--estimated-books", type=int, default=195, help="Offline estimate of ALL entered-by books, including books outside --books (default: 195)")
    snapshot.add_argument("--estimated-documents-per-book", type=int, default=50, help="Offline estimate of captured documents per book (default: 50)")
    snapshot.add_argument("--read-confirmation-threshold", type=int, default=10000, help="Require AFFIRMATIVE above this estimated read count (default: 10000; not a hard cap)")

    extract = commands.add_parser("extract", help="Local predictions only; paid calls require --execute and confirmation")
    extract.add_argument("--snapshot", type=Path, required=True)
    extract.add_argument("--out", type=Path, required=True, help="New run directory, or existing directory to resume")
    extract.add_argument("--config", type=Path, default=PACKAGE / "baseline.json")
    extract.add_argument("--inputs", choices=("renders", "scans"), default="renders")
    extract.add_argument("--scan-root", type=Path, default=DEFAULT_SCAN_ROOT)
    extract.add_argument("--inputs-manifest", type=Path, help=TuningHelp.inputs_manifest)
    extract.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE_ROOT,
                         help=TuningHelp.reference_root)
    extract.add_argument("--published-text", type=Path, help=TuningHelp.published_text)
    extract.add_argument("--limit", type=int, help="Number of COMPLETE books (20 for the first subset)")
    extract.add_argument("--books", nargs="+", help="Exact book IDs; interpreted as a purposive subset")
    extract.add_argument("--challenge-books", nargs="+", help="Reviewed challenge IDs; sampled results are reported separately")
    extract.add_argument("--seed", type=int, default=20261104)
    extract.add_argument("--replicate", default="1", help="Independent response cache identity; never select the best replicate")
    extract.add_argument("--max-spend-usd", type=float, default=16)
    extract.add_argument("--cycle-cap-usd", type=float, help=CYCLE_CAP_HELP)
    extract.add_argument("--ledger", type=Path, help="Shared evaluator ledger; defaults to snapshot parent's spend.sqlite")
    extract.add_argument("--secrets", default=".streamlit/secrets.toml", help="Read only AFTER paid-call confirmation")
    extract.add_argument("--freeze-dir", type=Path, default=DEFAULT_FREEZE_DIR, help=TuningHelp.freeze_dir)
    extract.add_argument("--rehearsal-lock", type=Path, help=TuningHelp.lock)
    extract.add_argument("--cohort", choices=("tune", "rehearsal", "proof"), default="tune", help=TuningHelp.cohort)
    group = extract.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true")
    group.add_argument("--dry-run", action="store_true", help="Default: local preprocessing/cache replay, zero network calls")

    headroom = commands.add_parser("headroom", help=HEADROOM_HELP)
    headroom.add_argument("--ledger", type=Path, default=evidence_dir() / 'spend.sqlite')

    score = commands.add_parser("score", help="Pure offline scoring; never reads credentials or creates a cloud client")
    score.add_argument("--snapshot", type=Path, required=True)
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--out", type=Path, help="Defaults to run/reports")
    score.add_argument("--protocol", type=Path, default=PACKAGE / "protocol_v3.json")
    score.add_argument("--adjudications", type=Path, help="Reviewed versioned reference overlay; original scores retained")
    score.add_argument("--equivalences", type=Path, help="Reviewed versioned name/imprint equivalences")
    score.add_argument("--compare-run", type=Path, help="Paired book-cluster comparison against another local run")
    score.add_argument("--freeze-dir", type=Path, help=TuningHelp.freeze_dir)
    score.add_argument("--cohort", choices=("tune", "rehearsal", "proof"), default="tune", help=TuningHelp.cohort)
    from .scoring import TEXT_OUTPUTS
    score.add_argument('--text-output', choices=TEXT_OUTPUTS, default='stored',
                       help=TuningHelp.text_output)
    audit = commands.add_parser("audit-tuning", help=TuningHelp.audit)
    audit.add_argument("--run", type=Path, required=True)
    audit.add_argument("--freeze-dir", type=Path, help=TuningHelp.freeze_dir)
    inventory = commands.add_parser("inventory", help="Offline scan filenames, page counts and hashes only")
    inventory.add_argument("--snapshot", type=Path, required=True)
    inventory.add_argument("--out", type=Path, required=True, help="Inventory JSON file")
    inventory.add_argument("--run", type=Path, required=True, help="Directory for the cohort admission record")
    inventory.add_argument("--scan-root", type=Path, default=DEFAULT_SCAN_ROOT)
    inventory.add_argument("--freeze-dir", type=Path, default=DEFAULT_FREEZE_DIR)
    inventory.add_argument("--cohort", choices=("tune", "rehearsal", "proof"), default="tune")
    from .tuning_loop import MEASURES
    from text_content.evaluation import TuningLoopText as T
    noise = commands.add_parser('tuning-noise', help=T.noise_help)
    noise.add_argument('--runs', type=Path, nargs='+', required=True, help=T.scored_help)
    noise.add_argument('--out', type=Path, required=True)
    attempt = commands.add_parser('tuning-attempt', help=T.attempt_help)
    attempt.add_argument('--baseline', type=Path, nargs='+', required=True, help=T.arm_help)
    attempt.add_argument('--baseline-check', type=Path, nargs='+', help=T.check_arm_help)
    attempt.add_argument('--candidate', type=Path, nargs='+', required=True, help=T.arm_help)
    attempt.add_argument('--candidate-check', type=Path, nargs='+', help=T.check_arm_help)
    attempt.add_argument('--floors', type=Path, required=True)
    attempt.add_argument('--target', choices=MEASURES, required=True, help=T.target_help)
    attempt.add_argument('--hypothesis', required=True)
    attempt.add_argument('--change', required=True)
    attempt.add_argument('--log', type=Path, required=True, help=T.log_help)
    return parser


def _inventory(args):
    from collections import Counter
    from .scan_inputs import inventory
    with run_lock(args.run):
        loader = TuningLoader(args.run, args.freeze_dir, cohort=args.cohort)
        with loader.activate():
            try:
                result = inventory(args.snapshot, loader.allowed, args.scan_root)
                atomic_json(args.out, result)
                groups = ({'iterate': loader.iterate, 'check': loader.check}
                          if args.cohort == 'tune' else {args.cohort: loader.allowed})
                counts = {}
                for name, ids in groups.items():
                    mapped = [row for row in result['books'] if row['id'] in ids]
                    unmapped = [row for row in result['unmapped'] if row['id'] in ids]
                    counts[name] = {'mapped': len(mapped), 'unmapped': len(unmapped),
                                    'pages': sum(row['page_count'] for row in mapped),
                                    'unmapped_reasons': dict(Counter(row['reason'] for row in unmapped))}
                counts['total_pages'] = sum(row['page_count'] for row in result['books'])
                print(json.dumps(counts, indent=2))
            finally:
                loader.audit()
    return 0


def _load_snapshot(path, *, loader=None, book_ids=None, additional_ids=None):
    if loader is None:
        raise ValueError(TuningErrors.missing_record)
    manifest = loader.snapshot(path, book_ids, additional_ids=additional_ids)
    if manifest.get("complete") is not True:
        raise ValueError("Snapshot has not completed")
    consistency = manifest.get("source_consistent")
    if consistency is False:
        raise ValueError("Snapshot changed during capture; inspect audit and create a new snapshot")
    if consistency is not True:
        audits = manifest.get("audits")
        explicit = isinstance(audits, dict) and all(
            type(audits.get(name)) is bool for name in ("source_changes", "membership"))
        if "source_consistent" not in manifest or consistency is not None or not explicit or all(audits.get(name) for name in ("source_changes", "membership")):
            raise ValueError("Snapshot has missing or invalid source-audit provenance")
    return manifest


def _load_calls(path, loader):
    from .json_sources import JsonSources
    with JsonSources(path) as source:
        records = source.items(source.root)
        aggregate_fields = {'aggregate_only', 'call_count', 'cost_usd', 'reserved_usd', 'state'}
        for span in records:
            fields = source.members(span)
            if (set(fields) == aggregate_fields
                    and source.decode(fields['aggregate_only']) is True
                    and source.decode(fields['state']) == 'aggregate'
                    and all(isinstance(source.decode(fields[key]), (int, float))
                            for key in ('call_count', 'cost_usd', 'reserved_usd'))):
                continue
            book_id = source.decode(fields['book_id']) if 'book_id' in fields else None
            loader.load([book_id], 'call_report', lambda: None)
        return [source.decode(span) for span in records]


def _snapshot(args):
    from google.api_core.exceptions import ResourceExhausted
    from .read_cost import estimate_snapshot_reads
    from .reporting import snapshot_audit_description
    from .snapshot import _check_audits, _check_selection, create_live_backend, create_snapshot
    source_changes = args.audit or args.audit_source_changes
    membership = args.audit or args.audit_membership
    if args.read_confirmation_threshold < 0:
        raise ValueError("--read-confirmation-threshold must be non-negative")
    # The estimate and authorization precede credentials, clients and reads.
    with run_lock(args.out):
        audits = {"source_changes": source_changes, "membership": membership}
        for name in ("manifest.json", "inventory.json", "inventory-progress.json"):
            state_path = args.out / name
            if state_path.exists():
                state = read_json(state_path)
                _check_selection(state, args.entered_by, args.books)
                _check_audits(state, audits)
                break
        audit_path = args.out / "audit-progress.json"
        if audit_path.exists():
            _check_audits(read_json(audit_path), audits)
        plan = estimate_snapshot_reads(args.out, books=args.books,
            estimated_books=args.estimated_books,
            estimated_documents_per_book=args.estimated_documents_per_book,
            audit_source_changes=source_changes, audit_membership=membership)
        print("Estimated Firestore document reads before any service access:")
        print(json.dumps(plan, indent=2), flush=True)
        if plan.get("estimated_firestore_reads", 0) > args.read_confirmation_threshold:
            try:
                answer = input("Type AFFIRMATIVE to authorize these estimated Firestore reads: ")
            except EOFError:
                answer = ""
            if answer != "AFFIRMATIVE":
                print("No Firestore reads authorized.")
                return 1
        try:
            backend = None if (args.out / "manifest.json").exists() else create_live_backend(args.secrets, project=args.project)
            manifest = create_snapshot(args.out, backend, entered_by=args.entered_by, books=args.books,
                                       audit_source_changes=source_changes, audit_membership=membership)
        except ResourceExhausted:
            print(f"Project {args.project!r}'s Firestore quota is exhausted. Stopped immediately; no quota retries. "
                  "The daily free-tier quota resets around midnight US/Pacific. "
                  f"Captured progress is preserved in {args.out}. After the quota resets, resume by rerunning "
                  f"the same snapshot command and options with --out {shlex.quote(str(args.out))}; "
                  "saved documents will be reused.", file=sys.stderr)
            return 2
    print(f"Snapshot {manifest.get('snapshot_id')}: {len(manifest.get('books', []))} books; {len(manifest.get('excluded_books', []))} exclusions; {len(manifest.get('audit', []))} audit findings")
    print(snapshot_audit_description(manifest))
    return 2 if manifest.get("source_consistent") is False else 0


def _live_anthropic(secrets_path, config):
    # Credentials are read only after confirmation; client creation is shared.
    from scripts.data_cleanup import load_secrets
    from utilities import get_anthropic_client
    from .billing import validate_billing
    validate_billing(config)
    key = validate_billing(config, load_secrets(secrets_path))
    return get_anthropic_client(api_key=key, max_retries=0)


def _live_provider(secrets_path, config):
    from .openai_client import OPENAI_PROVIDERS, create_openai_client
    from .billing import validate_billing
    from text_content.pipeline_runtime import EVALUATION_PROVIDER_UNSUPPORTED
    provider = config.get('provider', 'anthropic-messages')
    if provider in {'anthropic', 'anthropic-messages'}:
        return _live_anthropic(secrets_path, config)
    if provider not in OPENAI_PROVIDERS:
        raise ValueError(EVALUATION_PROVIDER_UNSUPPORTED.format(provider=provider))
    from scripts.data_cleanup import load_secrets
    validate_billing(config)
    key = validate_billing(config, load_secrets(secrets_path))
    return create_openai_client(key)


def _extract(args):
    with run_lock(args.out):
        lock = read_json(args.rehearsal_lock) if args.rehearsal_lock else None
        loader = TuningLoader(args.out, args.freeze_dir, cohort=args.cohort,
                              rehearsal_lock=lock)
        with loader.activate():
            try:
                return _extract_loaded(args, loader)
            finally:
                loader.audit()


def _extract_loaded(args, loader):
    from text_content.pipeline_prompts import PROMPT_VARIANTS
    from .gateway import Gateway, validate_prices
    from .runner import configure_inputs, estimate, freeze_run, run_extract, validate_inputs
    from .sampling import select_books
    from .store import Ledger
    from .billing import billing_provider, cycle_budgets
    config = read_json(args.config)
    budgets = cycle_budgets(config, args.cycle_cap_usd, required=args.execute)
    paired_inputs = None
    if args.inputs == 'scans':
        from .inputs_manifest import load_manifest, extraction_manifest
        if args.inputs_manifest is None:
            raise ValueError(TuningErrors.inputs_manifest_required)
        requested = args.books
        if not requested:
            saved = loader.run_record(args.out / 'run.json')
            previous_inputs = saved.get('contract', {}).get('config', {}).get('inputs_manifest')
            if previous_inputs is not None:
                requested = [row['id'] for row in previous_inputs['books']]
        if requested is not None:
            requested = sorted(set(requested) | set(args.challenge_books or []))
        paired_inputs = load_manifest(args.inputs_manifest, requested)
        snapshot_ids = [row['id'] for row in paired_inputs['books']
                        if row.get('reason') != 'missing_snapshot_id']
        manifest = _load_snapshot(args.snapshot, loader=loader, book_ids=snapshot_ids)
        input_manifest = extraction_manifest(manifest, paired_inputs)
    else:
        requested = args.books or loader.state.get('snapshot_book_ids')
        manifest = _load_snapshot(args.snapshot, loader=loader, book_ids=requested,
                                  additional_ids=args.challenge_books)
        input_manifest = manifest
    required = read_json(PACKAGE / "baseline.json").get("settings", {})
    settings = config.get("settings", {})
    for key, default in required.items():
        value = settings.get(key)
        if type(value) is not type(default):
            raise ValueError(f"Missing/invalid frozen setting: {key}")
    if settings["prompt_variant"] not in PROMPT_VARIANTS:
        raise ValueError(f"Unknown prompt variant: {settings['prompt_variant']!r}; "
                         f"choose one of {', '.join(PROMPT_VARIANTS)}")
    for key in ("extraction_max_edge", "locate_max_edge"):
        if not 512 <= settings.get(key, 0) <= 4096:
            raise ValueError(f"Invalid resolution: {key}")
    if not 256 <= settings.get("extraction_max_tokens", 0) <= 8192:
        raise ValueError("Invalid extraction_max_tokens")
    validate_prices(config, executing=args.execute)
    config = configure_inputs(config, args.inputs, inputs_manifest=paired_inputs,
                              scan_root=args.scan_root, reference_root=args.reference_root,
                              published_path=args.published_text)
    ledger_path = args.ledger or args.snapshot.parent / "spend.sqlite"
    saved_selection = args.out / "selection.json"
    if saved_selection.exists() and args.limit is None and args.books is None and args.challenge_books is None:
        selection = loader.selection(saved_selection)
    else:
        selection = select_books(input_manifest, limit=args.limit, books=args.books, seed=args.seed, challenge_books=args.challenge_books)
    if not selection.get("book_ids"):
        raise ValueError("No eligible books selected")
    if args.inputs == 'renders':
        if any(row.get("issue") == "ambiguous_book_folder" for row in manifest.get("audit", []) if set(row.get("book_ids", [])) & set(selection.get("book_ids", []))):
            raise ValueError("Selected books share an ambiguous source folder; resolve before extracting")
        validate_inputs(args.snapshot, manifest, selection, loader=loader)
    previous_path = args.out / "predictions.json"
    if not previous_path.exists():
        previous_path = args.out / "dry_run_predictions.json"
    previous = loader.predictions(previous_path) if previous_path.exists() else None
    plan = estimate(input_manifest, selection, previous)
    print(json.dumps(plan, indent=2))
    provider = billing_provider(config.get('provider', 'anthropic-messages'))
    cycle_cap = f"${budgets[provider]['effective_cap_usd']:.2f}" if provider in budgets else CYCLE_UNCONFIGURED
    print(RUN_BUDGET.format(cap=args.max_spend_usd, provider=provider, cycle_cap=cycle_cap, ledger=ledger_path))
    # No credentials, SDK client, lookups or paid requests before this point.
    if args.execute:
        answer = input("Type AFFIRMATIVE to allow metered paid calls up to this run cap: ")
        if answer != "AFFIRMATIVE":
            print("No paid calls authorized.")
            return 1
        temporary_dir = Path(tempfile.gettempdir()).resolve()
        for label, path in (("ledger", ledger_path), ("output directory", args.out)):
            resolved = path.resolve()
            if resolved.is_relative_to(temporary_dir):
                raise ValueError(
                    f"Paid run refused: {label} {path} resolves to {resolved} inside "
                    f"temporary directory {temporary_dir}. Keep paid-run evidence in "
                    f"durable storage, such as {evidence_dir()}.")
        from .billing import validate_billing
        validate_billing(config)
    frozen = freeze_run(args.out, manifest, selection, config, args.replicate, args.max_spend_usd, args.cycle_cap_usd, ledger_path)
    ledger = Ledger(ledger_path, frozen.get("run_id"), args.max_spend_usd, budgets=budgets)
    client = None
    try:
        ledger.recover()
        if args.execute:
            client = _live_provider(args.secrets, config)
        gateway = Gateway(client, ledger, config, replicate=args.replicate, dry_run=not args.execute)
        result = run_extract(args.snapshot, input_manifest, selection, args.out, gateway, config, loader=loader, dry_run=not args.execute)
        from .reporting import report_calls
        atomic_json(args.out / "calls.json", report_calls(ledger.rows(), loader))
        if result.get('stop_reason'):
            from text_content.pipeline_runtime import RUN_STOPPED
            reason, detail = result.get('stop_reason')
            print(RUN_STOPPED.format(reason=reason, detail=detail), file=sys.stderr)
        if not args.execute:
            print("Estimate after local processing and response-cache replay:")
            print(json.dumps(estimate(input_manifest, selection, result), indent=2))
        print(f"Completed local stages; {gateway.paid_calls} paid calls, {gateway.cache_hits} response-cache hits. Incomplete: {result.get('incomplete')}")
        return 2 if args.execute and result.get("incomplete") else 0
    finally:
        if client is not None:
            client.close()
        ledger.close()


def _score(args):
    state = TuningLoader.run_state(args.run / 'run.json')
    if not state:
        raise ValueError(TuningErrors.missing_record)
    with run_lock(args.run):
        loader = TuningLoader(args.run, args.freeze_dir or state['freeze_dir'],
                              cohort=args.cohort)
        with loader.activate():
            try:
                return _score_loaded(args, loader)
            finally:
                loader.audit()


def _score_loaded(args, loader):
    from .reporting import snapshot_audit_description, write_reports
    run_config = loader.run_record(args.run / "run.json")
    scans = run_config.get('contract', {}).get('config', {}).get('inputs') == 'scans'
    if scans and (args.adjudications or args.equivalences or args.compare_run):
        raise ValueError(TuningErrors.scan_text_only)
    if not scans and getattr(args, 'text_output', 'stored') != 'stored':
        raise ValueError(TuningErrors.text_output_scans_only)
    manifest = _load_snapshot(args.snapshot, loader=loader,
                              book_ids=loader.state.get('snapshot_book_ids'))
    protocol = read_json(args.protocol)
    # Read/freeze overlays before even opening predictions.
    overlay = loader.source_json(args.adjudications, 'historical_labels') if args.adjudications else None
    if args.compare_run and overlay and overlay.get("schema_version") != "adjudication-v2":
        from text_content.evaluation import EvaluationErrors
        raise ValueError(EvaluationErrors.comparison_overlay_required)
    if args.equivalences:
        from .scoring import apply_equivalences
        table = loader.source_json(args.equivalences, 'equivalences')
        protocol = apply_equivalences(protocol, table)
    adjudicated = None
    if overlay:
        from .scoring import apply_overlay
        adjudicated = apply_overlay(manifest, overlay, convention_version=protocol.get("convention_version"))
    from .store import digest
    contract = run_config.get("contract", {})
    if contract.get("snapshot_id") != manifest.get("snapshot_id") or contract.get("snapshot_hash") != digest(manifest):
        raise ValueError("Run and snapshot identities differ")
    predictions = loader.predictions(args.run / 'predictions.json')
    if predictions.get("snapshot_id") != manifest.get("snapshot_id"):
        raise ValueError("Prediction and snapshot identities differ")
    predictions["_run_dir"] = str(args.run.resolve())
    selection = loader.selection(args.run / "selection.json")
    if selection != contract.get("selection"):
        raise ValueError("Selection differs from the frozen run contract")
    if scans:
        return _score_scans(args, contract, predictions, selection, manifest, run_record=run_config)
    ledger_path = contract.get("ledger")
    if ledger_path and Path(ledger_path).is_file():
        from .store import read_calls
        calls = read_calls(ledger_path, run_config.get("run_id"))
    else:
        calls = _load_calls(args.run / "calls.json", loader) if (args.run / "calls.json").exists() else []
    manifest["_snapshot_dir"] = str(args.snapshot.resolve())
    out = args.out or args.run / "reports"
    summary = write_reports(manifest, predictions, out, protocol=protocol, selection=selection, calls=calls, loader=loader)
    corrected_summary = None
    if adjudicated:
        adjudicated["_snapshot_dir"] = manifest.get("_snapshot_dir")
        corrected_summary = write_reports(adjudicated, predictions, out / "adjudicated", protocol=protocol, selection=selection, calls=calls, loader=loader)
    if args.compare_run:
        from .comparison import compare_metrics
        candidate_state = TuningLoader.run_state(args.compare_run / "run.json")
        if not candidate_state:
            raise ValueError(TuningErrors.missing_record)
        TuningLoader(args.compare_run, args.freeze_dir or candidate_state['freeze_dir'],
                     cohort=args.cohort).audit()
        candidate_run = loader.run_record(args.compare_run / "run.json")
        other_contract = candidate_run.get("contract", {})
        if other_contract.get('config', {}).get('inputs') == 'scans':
            raise ValueError(TuningErrors.scan_text_only)
        if other_contract.get("snapshot_hash") != contract.get("snapshot_hash"):
            raise ValueError("Comparison runs must use the same frozen snapshot")
        candidate = loader.predictions(args.compare_run / 'predictions.json')
        if candidate.get("snapshot_id") != manifest.get("snapshot_id"):
            raise ValueError("Candidate predictions use a different snapshot")
        candidate["_run_dir"] = str(args.compare_run.resolve())
        other_selection = loader.selection(args.compare_run / "selection.json")
        if other_selection != other_contract.get("selection"):
            raise ValueError("Comparison selection differs from its frozen run contract")
        comparison_summary = write_reports(manifest, candidate, out / "comparison_candidate", protocol=protocol, selection=other_selection, loader=loader)
        comparison = {"raw_to_raw": compare_metrics(summary.get("book_scores", []), comparison_summary.get("book_scores", [])),
                      "corrected_to_corrected": None, "fixed_baseline_measurement_repair": None}
        if adjudicated is not None and corrected_summary is not None:
            corrected_candidate = write_reports(adjudicated, candidate, out / "comparison_candidate" / "adjudicated", protocol=protocol, selection=other_selection, loader=loader)
            comparison["corrected_to_corrected"] = compare_metrics(corrected_summary.get("book_scores", []), corrected_candidate.get("book_scores", []))
            comparison["fixed_baseline_measurement_repair"] = compare_metrics(summary.get("book_scores", []), corrected_summary.get("book_scores", []), measurement_repair=True)
        atomic_json(out / "comparison.json", comparison)
    print(f"Offline report: {out / 'summary.json'}")
    print(snapshot_audit_description(manifest))
    return 0


def _score_scans(args, contract, predictions, selection, manifest, *, run_record=None):
    from .alias_metrics import score_aliases
    from .inputs_manifest import scoring_inputs
    from .normalization import NORMALIZATION_VERSION
    from .scoring import score_book_text, score_character_recall, score_character_attributes
    from .outcomes import normalize_outcome
    config = contract['config']
    inputs = config['inputs_manifest']
    selected = set(selection['book_ids'])
    inputs = {**inputs, 'books': [row for row in inputs['books'] if row['id'] in selected]}
    if {row['id'] for row in inputs['books']} != selected:
        raise ValueError(TuningErrors.scan_selection_missing)
    rows = scoring_inputs(inputs, predictions, config['reference_root'],
                          published_path=config.get('published_path'), text_output=args.text_output)
    scores = []
    references = {book['id']: book.get('reference', {}).get('characters', {})
                  for book in manifest['books']}
    for row in rows:
        score = score_book_text([row['reference_text'] or ''], [row['prediction_text']],
                                text_output=args.text_output, forced_score=row['forced_score'])
        reference = references.get(row['id'], {})
        book_prediction = predictions.get('books', {}).get(row['id'], {})
        prediction = normalize_outcome(book_prediction.get('characters') or {})
        predicted = prediction.get('value')
        if prediction.get('status') == 'completed' and not isinstance(predicted, list):
            prediction = {**prediction, 'status': 'failed', 'failure_kind': 'invalid_response'}
        if row['status'] == 'unavailable' or prediction.get('status') != 'completed' or not isinstance(predicted, list):
            predicted = []
        characters = score_character_recall(reference.get('value') or [], predicted)
        characters.update(reference_status=reference.get('status', 'missing'),
                          prediction_status=prediction.get('status', 'pending'),
                          failure_kind=prediction.get('failure_kind'))
        pages = [(page_id, page.get('text') or '')
                 for page_id, page in book_prediction.get('pages', {}).items()
                 if page.get('status') == 'completed']
        scores.append({**score, 'id': row['id'], 'status': row['status'],
                       'reason': row['reason'], 'failed_pages': row['failed_pages'],
                       'page_failures': row['page_failures'],
                       'unrun_pages': row['unrun_pages'],
                       'characters': characters,
                       'attributes': score_character_attributes(characters),
                       'aliases': score_aliases(characters, pages)})
    summary = {'measure': 'book-token-edit-accuracy-v1', 'snapshot_id': contract['snapshot_id'],
               'text_output': args.text_output, 'normalization_version': NORMALIZATION_VERSION,
               'book_denominator': len(scores), 'book_scores': scores}
    from .freeze import load_selections
    from .tuning import current_loader
    from .reporting import summarize_scan_scores, scan_summary_markdown, _atomic_text
    loader = current_loader()
    selections = load_selections(loader.selections_path)
    summary['cohort_summary'] = summarize_scan_scores(scores, selections, cohort=loader.cohort)
    out = args.out or args.run / 'reports'
    atomic_json(out / 'book_scores.json', scores)
    atomic_json(out / 'summary.json', summary)
    _atomic_text(out / 'summary.md', scan_summary_markdown(summary['cohort_summary']))
    if loader.cohort == 'tune' and run_record is not None:
        from .tuning_loop import tuning_scores
        from .store import digest
        # Read only ledger totals, never request/response bodies for this report.
        cost = None
        ledger = contract.get('ledger')
        if ledger and Path(ledger).is_file():
            with sqlite3.connect(Path(ledger).resolve().as_uri() + '?mode=ro', uri=True) as db:
                settled, outstanding = db.execute(
                    "SELECT sum(cost)/1e9, sum(reservation)/1e9 FROM calls WHERE run_id=?",
                    (run_record['run_id'],)).fetchone()
            cost = {'settled_usd': settled or 0, 'outstanding_reservations_usd': outstanding or 0,
                    'per_book_usd': (settled or 0) / len(scores) if scores else None}
        numeric = tuning_scores(
            scores, selections, run_id=run_record['run_id'],
            configuration_id=digest({'config': config, 'sources': contract.get('sources')}),
            replicate=contract['replicate'], measurement_id=digest({
                'snapshot_id': contract['snapshot_id'], 'text_output': args.text_output,
                'normalization_version': NORMALIZATION_VERSION, 'scoring': 'scan-tuning-v1'}),
            reference_hashes={row['id']: digest({'input': row, 'characters': references.get(row['id'])})
                              for row in inputs['books']}, cost=cost)
        atomic_json(out / 'tuning_scores.json', numeric)
    print(f"Offline report: {out / 'summary.json'}")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return {"snapshot": _snapshot, "extract": _extract, "score": _score, "inventory": _inventory,
                "audit-tuning": _audit_tuning, 'tuning-noise': _tuning_noise,
                'tuning-attempt': _tuning_attempt, 'headroom': _headroom}.get(args.command)(args)
    except (OSError, ValueError, TypeError, sqlite3.Error, EOFError, TuningRefused, TuningExposure) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


def _headroom(args):
    from .store import read_headroom
    for row in read_headroom(args.ledger):
        print((HEADROOM_LINE if row['configured'] else HEADROOM_UNCONFIGURED).format(**row))
    return 0


def _tuning_noise(args):
    from .tuning_loop import noise_floors
    from text_content.evaluation import TuningLoopText as T
    floors = noise_floors([read_json(path) for path in args.runs])
    atomic_json(args.out, floors)
    print(T.floors_written.format(path=args.out, replicates=floors['replicates']))
    return 0


def _tuning_attempt(args):
    from .tuning_loop import append_attempt, format_verdict
    from text_content.evaluation import TuningLoopText as T
    def arm(paths, check_paths):
        runs = [read_json(path) for path in paths]
        return {'iterate': runs, 'check': [read_json(path) for path in check_paths] if check_paths else runs}

    row = append_attempt(args.log, arm(args.baseline, args.baseline_check),
                         arm(args.candidate, args.candidate_check),
                         read_json(args.floors), args.target, args.hypothesis, args.change)
    print(format_verdict(row))
    print(T.logged.format(**row))
    return 0


def _audit_tuning(args):
    with run_lock(args.run):
        state = TuningLoader.run_state(args.run / 'run.json')
        if not state:
            raise ValueError(TuningErrors.missing_record)
        TuningLoader(args.run, args.freeze_dir or state['freeze_dir'],
                     cohort=state['cohort']).audit()
    return 0
