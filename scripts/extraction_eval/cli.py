"""Three independently runnable operations; score has no service dependencies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sqlite3
import sys

from .store import atomic_json, read_json, run_lock

PACKAGE = Path(__file__).resolve().parent


def build_parser():
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
    extract.add_argument("--limit", type=int, help="Number of COMPLETE books (20 for the first subset)")
    extract.add_argument("--books", nargs="+", help="Exact IDs/titles; interpreted as a purposive subset")
    extract.add_argument("--challenge-books", nargs="+", help="Reviewed challenge IDs/titles; sampled results are reported separately")
    extract.add_argument("--seed", type=int, default=20261104)
    extract.add_argument("--replicate", default="1", help="Independent response cache identity; never select the best replicate")
    extract.add_argument("--max-spend-usd", type=float, default=16)
    extract.add_argument("--monthly-cap-usd", type=float, default=200)
    extract.add_argument("--ledger", type=Path, help="Shared evaluator ledger; defaults to snapshot parent's spend.sqlite")
    extract.add_argument("--secrets", default=".streamlit/secrets.toml", help="Read only AFTER paid-call confirmation")
    group = extract.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true")
    group.add_argument("--dry-run", action="store_true", help="Default: local preprocessing/cache replay, zero network calls")

    score = commands.add_parser("score", help="Pure offline scoring; never reads credentials or creates a cloud client")
    score.add_argument("--snapshot", type=Path, required=True)
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--out", type=Path, help="Defaults to run/reports")
    score.add_argument("--protocol", type=Path, default=PACKAGE / "protocol.json")
    score.add_argument("--adjudications", type=Path, help="Reviewed versioned reference overlay; original scores retained")
    score.add_argument("--equivalences", type=Path, help="Reviewed versioned name/imprint equivalences")
    score.add_argument("--compare-run", type=Path, help="Paired book-cluster comparison against another local run")
    return parser


def _load_snapshot(path):
    manifest = read_json(path / "manifest.json")
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


def _live_anthropic(secrets_path):
    # Documented standalone scripts exception (#129), same secrets loader as
    # data_cleanup. Never create Firestore or S3 clients in the extract command.
    from scripts.data_cleanup import load_secrets
    import anthropic
    secret = load_secrets(secrets_path).get("ANTHROPIC_API_KEY")
    if not secret:
        raise ValueError("ANTHROPIC_API_KEY is absent")
    return anthropic.Anthropic(api_key=secret, max_retries=0)


def _extract(args):
    from .gateway import Gateway, validate_prices
    from .runner import estimate, freeze_run, run_extract, validate_inputs
    from .sampling import select_books
    from .store import Ledger
    manifest = _load_snapshot(args.snapshot)
    config = read_json(args.config)
    required = read_json(PACKAGE / "baseline.json").get("settings", {})
    settings = config.get("settings", {})
    for key, default in required.items():
        value = settings.get(key)
        if type(value) is not type(default):
            raise ValueError(f"Missing/invalid frozen setting: {key}")
    for key in ("extraction_max_edge", "locate_max_edge"):
        if not 512 <= settings.get(key, 0) <= 4096:
            raise ValueError(f"Invalid resolution: {key}")
    if not 256 <= settings.get("extraction_max_tokens", 0) <= 8192:
        raise ValueError("Invalid extraction_max_tokens")
    validate_prices(config, executing=args.execute)
    ledger_path = args.ledger or args.snapshot.parent / "spend.sqlite"
    with run_lock(args.out):
        saved_selection = args.out / "selection.json"
        if saved_selection.exists() and args.limit is None and args.books is None and args.challenge_books is None:
            selection = read_json(saved_selection)
        else:
            selection = select_books(manifest, limit=args.limit, books=args.books, seed=args.seed, challenge_books=args.challenge_books)
        if not selection.get("book_ids"):
            raise ValueError("No eligible books selected")
        if any(row.get("issue") == "ambiguous_book_folder" for row in manifest.get("audit", []) if set(row.get("book_ids", [])) & set(selection.get("book_ids", []))):
            raise ValueError("Selected books share an ambiguous source folder; resolve before extracting")
        validate_inputs(args.snapshot, manifest, selection)
        previous_path = args.out / "predictions.json"
        if not previous_path.exists():
            previous_path = args.out / "dry_run_predictions.json"
        previous = read_json(previous_path) if previous_path.exists() else None
        plan = estimate(manifest, selection, previous)
        print(json.dumps(plan, indent=2))
        print(f"Run cap: ${args.max_spend_usd:.2f}; shared monthly cap: ${args.monthly_cap_usd:.2f}. Ledger: {ledger_path}")
        # No credentials, SDK client, lookups or paid requests before this point.
        if args.execute:
            answer = input("Type AFFIRMATIVE to allow metered paid calls up to this run cap: ")
            if answer != "AFFIRMATIVE":
                print("No paid calls authorized.")
                return 1
        frozen = freeze_run(args.out, manifest, selection, config, args.replicate, args.max_spend_usd, args.monthly_cap_usd, ledger_path)
        ledger = Ledger(ledger_path, frozen.get("run_id"), args.max_spend_usd, args.monthly_cap_usd)
        client = None
        try:
            ledger.recover()
            if args.execute:
                client = _live_anthropic(args.secrets)
            gateway = Gateway(client, ledger, config, replicate=args.replicate, dry_run=not args.execute)
            result = run_extract(args.snapshot, manifest, selection, args.out, gateway, config, dry_run=not args.execute)
            atomic_json(args.out / "calls.json", ledger.rows())
            if not args.execute:
                print("Estimate after local processing and response-cache replay:")
                print(json.dumps(estimate(manifest, selection, result), indent=2))
            print(f"Completed local stages; {gateway.paid_calls} paid calls, {gateway.cache_hits} response-cache hits. Incomplete: {result.get('incomplete')}")
            return 2 if args.execute and result.get("incomplete") else 0
        finally:
            if client is not None:
                client.close()
            ledger.close()


def _score(args):
    from .reporting import snapshot_audit_description, write_reports
    manifest = _load_snapshot(args.snapshot)
    protocol = read_json(args.protocol)
    # Read/freeze overlays before even opening predictions.
    overlay = read_json(args.adjudications) if args.adjudications else None
    if args.equivalences:
        from .scoring import apply_equivalences
        table = read_json(args.equivalences)
        protocol = apply_equivalences(protocol, table)
    adjudicated = None
    if overlay:
        from .scoring import apply_overlay
        adjudicated = apply_overlay(manifest, overlay)
    run_config = read_json(args.run / "run.json")
    from .store import digest
    contract = run_config.get("contract", {})
    if contract.get("snapshot_id") != manifest.get("snapshot_id") or contract.get("snapshot_hash") != digest(manifest):
        raise ValueError("Run and snapshot identities differ")
    predictions = read_json(args.run / "predictions.json")
    if predictions.get("snapshot_id") != manifest.get("snapshot_id"):
        raise ValueError("Prediction and snapshot identities differ")
    predictions["_run_dir"] = str(args.run.resolve())
    selection = read_json(args.run / "selection.json")
    if selection != contract.get("selection"):
        raise ValueError("Selection differs from the frozen run contract")
    ledger_path = contract.get("ledger")
    if ledger_path and Path(ledger_path).is_file():
        from .store import read_calls
        calls = read_calls(ledger_path, run_config.get("run_id"))
    else:
        calls = read_json(args.run / "calls.json") if (args.run / "calls.json").exists() else []
    manifest["_snapshot_dir"] = str(args.snapshot.resolve())
    out = args.out or args.run / "reports"
    summary = write_reports(manifest, predictions, out, protocol=protocol, selection=selection, calls=calls)
    if adjudicated:
        adjudicated["_snapshot_dir"] = manifest.get("_snapshot_dir")
        write_reports(adjudicated, predictions, out / "adjudicated", protocol=protocol, selection=selection, calls=calls)
    if args.compare_run:
        from .statistics import paired_comparison
        candidate_run = read_json(args.compare_run / "run.json")
        other_contract = candidate_run.get("contract", {})
        if other_contract.get("snapshot_hash") != contract.get("snapshot_hash"):
            raise ValueError("Comparison runs must use the same frozen snapshot")
        candidate = read_json(args.compare_run / "predictions.json")
        if candidate.get("snapshot_id") != manifest.get("snapshot_id"):
            raise ValueError("Candidate predictions use a different snapshot")
        candidate["_run_dir"] = str(args.compare_run.resolve())
        other_selection = read_json(args.compare_run / "selection.json")
        if other_selection != other_contract.get("selection"):
            raise ValueError("Comparison selection differs from its frozen run contract")
        comparison_summary = write_reports(manifest, candidate, out / "comparison_candidate", protocol=protocol, selection=other_selection)
        # reporting emits comparison-ready per-book rows under book_scores.
        comparison = paired_comparison(summary.get("book_scores", []), comparison_summary.get("book_scores", []), value="ocr_f1")
        atomic_json(out / "comparison.json", comparison)
    print(f"Offline report: {out / 'summary.json'}")
    print(snapshot_audit_description(manifest))
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return {"snapshot": _snapshot, "extract": _extract, "score": _score}.get(args.command)(args)
    except (OSError, ValueError, TypeError, sqlite3.Error, EOFError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")
