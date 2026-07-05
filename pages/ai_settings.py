"""Admin-only page to edit the global AI-pipeline parameters (models, image
resolutions, token caps and feature toggles) WITHOUT a code deploy.

Every value is stored in a single plain Firestore config doc
(``settings/ai_pipeline``) and read back through ``utilities.get_ai_settings``,
which validates each value and falls back to today's constant for anything
missing/invalid — so an empty config behaves exactly like the previous hardcoded
app (bar the approved 2000px extraction default).

Safety gate: the parameter controls and the Save button are DISABLED by default.
The admin must first flip an explicit "Enable editing of AI parameters" toggle,
which reveals a clear warning, before anything can be changed or saved.
"""

import streamlit as st
from google.api_core.exceptions import GoogleAPIError

from utilities import (
    page_layout,
    check_authentication_status,
    is_admin,
    get_ai_settings,
    save_ai_settings,
    get_api_usage_summary,
    AI_MODEL_ALLOWLIST,
    AI_EDGE_MIN,
    AI_EDGE_MAX,
    AI_TOKENS_MIN,
    AI_TOKENS_MAX,
    AI_USAGE_SUMMARY_DAYS,
)
from text_content import AdminSettings

check_authentication_status()

# Admin-only page (#83). A non-admin who reaches the URL gets a friendly denial.
if not is_admin():
    st.error(AdminSettings.not_admin)
    st.stop()

page_layout()

st.title(AdminSettings.title)
st.write(AdminSettings.intro)

settings = get_ai_settings()


def _model_index(key):
    """Guarded index into the model allow-list (#91): never assume the stored
    value is still a valid option — fall back to the first entry."""
    value = settings[key]
    return AI_MODEL_ALLOWLIST.index(value) if value in AI_MODEL_ALLOWLIST else 0


# ---------------------------------------------------------------------------
# Current effective settings + a rough per-page cost hint.
st.header(AdminSettings.current_values_header)
st.caption(AdminSettings.current_values_caption)
current_col, _ = st.columns([2, 1])
for field, value in settings.items():
    label = field.replace('_', ' ').capitalize()
    current_col.markdown(f"**{label}:** {value}")

# Rough Anthropic image-token estimate for a portrait page at the current edge
# (~pixels / 750, with a ~0.7 portrait aspect). Indicative only.
edge = settings['extraction_max_edge']
approx_tokens = int(edge * edge * 0.7 / 750)
st.caption(AdminSettings.cost_hint.format(edge=edge, tokens=approx_tokens))

st.divider()

# ---------------------------------------------------------------------------
# Safety gate — controls stay locked until this is explicitly enabled.
st.header(AdminSettings.safety_header)
editing_enabled = st.toggle(
    AdminSettings.enable_editing_label,
    value=False,
    key="ai_settings_enable_editing_toggle",
    help=AdminSettings.enable_editing_help,
)
if editing_enabled:
    st.warning(AdminSettings.editing_warning)
else:
    st.info(AdminSettings.editing_disabled_info)

disabled = not editing_enabled

# ---------------------------------------------------------------------------
# Control panel — the tunable AI parameters.
st.subheader(AdminSettings.controls_header)

st.markdown(f"**{AdminSettings.models_subheader}**")
extraction_model = st.selectbox(
    AdminSettings.extraction_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('extraction_model'), disabled=disabled,
    key="ai_settings_extraction_model_select",
)
metadata_model = st.selectbox(
    AdminSettings.metadata_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('metadata_model'), disabled=disabled,
    key="ai_settings_metadata_model_select",
)
character_detection_model = st.selectbox(
    AdminSettings.character_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('character_detection_model'), disabled=disabled,
    key="ai_settings_character_model_select",
)
locate_model = st.selectbox(
    AdminSettings.locate_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('locate_model'), disabled=disabled,
    key="ai_settings_locate_model_select",
)
rotation_model = st.selectbox(
    AdminSettings.rotation_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('rotation_model'), disabled=disabled,
    key="ai_settings_rotation_model_select",
)
crop_quality_model = st.selectbox(
    AdminSettings.crop_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('crop_quality_model'), disabled=disabled,
    key="ai_settings_crop_model_select",
)
theme_model = st.selectbox(
    AdminSettings.theme_model_label, options=AI_MODEL_ALLOWLIST,
    index=_model_index('theme_model'), disabled=disabled,
    key="ai_settings_theme_model_select",
)

st.markdown(f"**{AdminSettings.resolution_subheader}**")
extraction_max_edge = st.number_input(
    AdminSettings.extraction_edge_label,
    min_value=AI_EDGE_MIN, max_value=AI_EDGE_MAX,
    value=int(settings['extraction_max_edge']), step=16, disabled=disabled,
    help=AdminSettings.extraction_edge_help.format(min=AI_EDGE_MIN, max=AI_EDGE_MAX),
    key="ai_settings_extraction_edge_number",
)
locate_max_edge = st.number_input(
    AdminSettings.locate_edge_label,
    min_value=AI_EDGE_MIN, max_value=AI_EDGE_MAX,
    value=int(settings['locate_max_edge']), step=16, disabled=disabled,
    help=AdminSettings.locate_edge_help.format(min=AI_EDGE_MIN, max=AI_EDGE_MAX),
    key="ai_settings_locate_edge_number",
)
extraction_max_tokens = st.number_input(
    AdminSettings.extraction_tokens_label,
    min_value=AI_TOKENS_MIN, max_value=AI_TOKENS_MAX,
    value=int(settings['extraction_max_tokens']), step=128, disabled=disabled,
    help=AdminSettings.extraction_tokens_help.format(min=AI_TOKENS_MIN, max=AI_TOKENS_MAX),
    key="ai_settings_extraction_tokens_number",
)

st.markdown(f"**{AdminSettings.features_subheader}**")
enable_rotation_correction = st.toggle(
    AdminSettings.enable_rotation_label,
    value=bool(settings['enable_rotation_correction']), disabled=disabled,
    help=AdminSettings.enable_rotation_help,
    key="ai_settings_enable_rotation_toggle",
)
enable_crop_quality_gate = st.toggle(
    AdminSettings.enable_crop_gate_label,
    value=bool(settings['enable_crop_quality_gate']), disabled=disabled,
    help=AdminSettings.enable_crop_gate_help,
    key="ai_settings_enable_crop_gate_toggle",
)

st.divider()

if st.button(AdminSettings.save_button, disabled=disabled,
             key="ai_settings_save_button"):
    new_values = {
        'extraction_model': extraction_model,
        'metadata_model': metadata_model,
        'character_detection_model': character_detection_model,
        'locate_model': locate_model,
        'rotation_model': rotation_model,
        'crop_quality_model': crop_quality_model,
        'theme_model': theme_model,
        'extraction_max_edge': int(extraction_max_edge),
        'locate_max_edge': int(locate_max_edge),
        'extraction_max_tokens': int(extraction_max_tokens),
        'enable_rotation_correction': bool(enable_rotation_correction),
        'enable_crop_quality_gate': bool(enable_crop_quality_gate),
    }
    try:
        # save_ai_settings re-validates every value and clears the settings cache.
        save_ai_settings(new_values)
    except GoogleAPIError as exc:
        st.error(AdminSettings.save_error.format(error=exc))
    else:
        st.success(AdminSettings.save_success)
        st.rerun()


# ---------------------------------------------------------------------------
# Read-only API usage & cost dashboard. Every AI call is metered into a per-day
# Firestore doc (utilities.record_api_usage); this surfaces today's and the
# recent window's tokens + estimated $ by model and by flow.
st.divider()
st.header(AdminSettings.usage_header)
st.caption(AdminSettings.usage_caption)

if st.button(AdminSettings.usage_refresh_button, key="ai_settings_usage_refresh_button"):
    st.rerun()


def _fmt_cost(value):
    """Format a USD cost, keeping small amounts legible."""
    value = float(value or 0)
    return f"${value:.2f}" if value >= 0.01 else f"${value:.4f}"


def _fmt_int(value):
    """Thousands-separated integer for token/call counts."""
    return f"{int(value or 0):,}"


def _breakdown_rows(name_column, breakdown):
    """Turn a ``{name: metrics}`` map into display rows sorted by descending $."""
    rows = []
    for name, metrics in sorted(
        breakdown.items(), key=lambda kv: kv[1].get("cost_usd", 0), reverse=True
    ):
        rows.append({
            name_column: name,
            AdminSettings.usage_col_calls: _fmt_int(metrics.get("calls")),
            AdminSettings.usage_col_cost: _fmt_cost(metrics.get("cost_usd")),
            AdminSettings.usage_col_input: _fmt_int(metrics.get("input_tokens")),
            AdminSettings.usage_col_output: _fmt_int(metrics.get("output_tokens")),
            AdminSettings.usage_col_cache_read: _fmt_int(metrics.get("cache_read_tokens")),
            AdminSettings.usage_col_cache_write: _fmt_int(metrics.get("cache_write_tokens")),
        })
    return rows


try:
    usage_summary = get_api_usage_summary(days=AI_USAGE_SUMMARY_DAYS)
except GoogleAPIError as exc:
    st.error(AdminSettings.usage_load_error.format(error=exc))
    usage_summary = None

if usage_summary is not None:
    window = usage_summary["window"]
    window_totals = window["totals"]
    days = usage_summary["window_days"]

    if not usage_summary["daily"]:
        st.info(AdminSettings.usage_no_data)
    else:
        today = usage_summary["today"] or {}
        st.subheader(AdminSettings.usage_today_header)
        t1, t2, t3, t4 = st.columns(4)
        t1.metric(AdminSettings.usage_metric_cost, _fmt_cost(today.get("cost_usd")))
        t2.metric(AdminSettings.usage_metric_calls, _fmt_int(today.get("calls")))
        t3.metric(AdminSettings.usage_metric_input, _fmt_int(today.get("input_tokens")))
        t4.metric(AdminSettings.usage_metric_output, _fmt_int(today.get("output_tokens")))

        st.subheader(AdminSettings.usage_window_header.format(days=days))
        w1, w2, w3, w4 = st.columns(4)
        w1.metric(AdminSettings.usage_metric_cost, _fmt_cost(window_totals.get("cost_usd")))
        w2.metric(AdminSettings.usage_metric_calls, _fmt_int(window_totals.get("calls")))
        w3.metric(AdminSettings.usage_metric_input, _fmt_int(window_totals.get("input_tokens")))
        w4.metric(AdminSettings.usage_metric_output, _fmt_int(window_totals.get("output_tokens")))

        st.markdown(f"**{AdminSettings.usage_by_model_header.format(days=days)}**")
        st.table(_breakdown_rows(AdminSettings.usage_col_model, window["by_model"]))

        st.markdown(f"**{AdminSettings.usage_by_flow_header.format(days=days)}**")
        st.table(_breakdown_rows(AdminSettings.usage_col_flow, window["by_flow"]))

        st.markdown(f"**{AdminSettings.usage_daily_header.format(days=days)}**")
        st.table([
            {
                AdminSettings.usage_col_date: entry["date"],
                AdminSettings.usage_col_calls: _fmt_int(entry["calls"]),
                AdminSettings.usage_col_cost: _fmt_cost(entry["cost_usd"]),
                AdminSettings.usage_col_input: _fmt_int(entry["input_tokens"]),
                AdminSettings.usage_col_output: _fmt_int(entry["output_tokens"]),
            }
            for entry in usage_summary["daily"]
        ])
