"""Snapshot audit coverage remains explicit in offline score artifacts."""

import json
import socket

import pytest

from scripts.extraction_eval.reporting import write_reports


@pytest.mark.parametrize(
    "audit_fields, expected_audits, expected_status, expected_consistency, description",
    [
        ({"audits": {"source_changes": False, "membership": False}, "source_consistent": None},
         {"source_changes": False, "membership": False}, "not_audited", None,
         "Snapshot audits: NOT AUDITED. Source-change audit: not run; membership audit: not run. Source consistency: not verified."),
        ({"audits": {"source_changes": True, "membership": False}, "source_consistent": None},
         {"source_changes": True, "membership": False}, "partially_audited", None,
         "Snapshot audits: PARTIALLY AUDITED. Source-change audit: ran; membership audit: not run. Source consistency: not verified."),
        ({"audits": {"source_changes": False, "membership": True}, "source_consistent": None},
         {"source_changes": False, "membership": True}, "partially_audited", None,
         "Snapshot audits: PARTIALLY AUDITED. Source-change audit: not run; membership audit: ran. Source consistency: not verified."),
        ({"audits": {"source_changes": True, "membership": True}, "source_consistent": True},
         {"source_changes": True, "membership": True}, "audited", True,
         "Snapshot audits: AUDITED. Source-change audit: ran; membership audit: ran. Source consistency: verified."),
        ({"audits": {"source_changes": True, "membership": False}, "source_consistent": False},
         {"source_changes": True, "membership": False}, "partially_audited", False,
         "Snapshot audits: PARTIALLY AUDITED. Source-change audit: ran; membership audit: not run. Source consistency: changes detected."),
        ({"source_consistent": True}, {"source_changes": None, "membership": None}, "unknown", None,
         "Snapshot audits: UNKNOWN. Source-change audit: unknown; membership audit: unknown. Source consistency: not verified."),
        ({"audits": {"source_changes": "true"}, "source_consistent": True},
         {"source_changes": None, "membership": None}, "unknown", None,
         "Snapshot audits: UNKNOWN. Source-change audit: unknown; membership audit: unknown. Source consistency: not verified."),
        ({"audits": {"source_changes": False, "membership": False}, "source_consistent": True},
         {"source_changes": False, "membership": False}, "not_audited", None,
         "Snapshot audits: NOT AUDITED. Source-change audit: not run; membership audit: not run. Source consistency: not verified."),
    ],
)
def test_score_reports_expose_audit_coverage_offline(
    tmp_path, monkeypatch, audit_fields, expected_audits, expected_status, expected_consistency, description
):
    def no_network(*args, **kwargs):
        pytest.fail("Audit provenance reporting attempted a network connection")

    monkeypatch.setattr(socket, "socket", no_network)
    manifest = {"snapshot_id": "snapshot", "books": [], **audit_fields}
    predictions = {"run_id": "run", "books": {}}
    summary = write_reports(manifest, predictions, tmp_path)
    saved = json.loads((tmp_path / "summary.json").read_text())
    assert summary == saved
    assert saved.get("snapshot_audits") == expected_audits
    assert saved.get("snapshot_audit_status") == expected_status
    assert saved.get("snapshot_source_consistent") is expected_consistency
    assert description in (tmp_path / "inspection.html").read_text()
