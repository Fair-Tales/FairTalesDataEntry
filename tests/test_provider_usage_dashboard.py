"""Exercise the usage dashboard without importing its live Streamlit page."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from text_content import AdminSettings


def _render(metrics):
    tree = ast.parse(
        (Path(__file__).parent.parent / "pages" / "ai_settings.py").read_text()
    )
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_fmt_cost", "_fmt_int", "_breakdown_rows"}
    ]
    branch = next(
        node for node in tree.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "usage_summary is not None"
    )
    events = {"warnings": [], "metrics": [], "tables": []}
    column = SimpleNamespace(metric=lambda *args: events["metrics"].append(args))
    totals = {
        "cost_usd": 0,
        "calls": 1,
        "input_tokens": 12,
        "output_tokens": 3,
        **metrics,
    }
    namespace = {
        "AdminSettings": AdminSettings,
        "st": SimpleNamespace(
            warning=events["warnings"].append,
            columns=lambda count: [column] * count,
            subheader=lambda *args: None,
            markdown=lambda *args: None,
            table=events["tables"].append,
        ),
        "usage_summary": {
            "today": totals,
            "window_days": 30,
            "window": {
                "totals": totals,
                "by_provider": {"openai": totals},
                "by_model": {"example-model": totals},
                "by_flow": {"extraction": totals},
            },
            "daily": [{"date": "2026-11-07", **totals}],
        },
    }
    exec(compile(ast.Module(body=[*helpers, branch], type_ignores=[]),
                 "pages/ai_settings.py", "exec"), namespace)
    return events


@pytest.mark.parametrize("gap", ["unpriced_calls", "unknown_usage_calls"])
def test_unknown_cost_is_marked_incomplete_in_every_display(gap):
    events = _render({gap: 1})
    assert len(events["warnings"]) == 1
    assert "known subtotals" in events["warnings"][0]
    cost_values = [
        value for label, value in events["metrics"]
        if label == AdminSettings.usage_metric_cost
    ]
    cost_values.extend(
        row[AdminSettings.usage_col_cost]
        for table in events["tables"] for row in table
    )
    assert len(cost_values) == 6  # Today, window, provider, model, flow, day.
    assert all(value == "$0.0000 (incomplete)" for value in cost_values)
    provider = events["tables"][0][0]
    assert provider[AdminSettings.usage_col_provider] == "openai"
    assert provider[AdminSettings.usage_col_input] == "12"


def test_legacy_priced_metrics_need_no_gap_fields():
    events = _render({"cost_usd": 0.125})
    assert events["warnings"] == []
    assert events["metrics"][0] == (AdminSettings.usage_metric_cost, "$0.12")
    assert all(
        row[AdminSettings.usage_col_cost] == "$0.12"
        for table in events["tables"] for row in table
    )
