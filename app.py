from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ai_agent import AnalystConnectorError, ask_analyst_agent, compact_context, local_analyst_brief
from core import (
    DataLoadError,
    FormulaError,
    apply_calculated_fields,
    detect_date_columns,
    excel_sheet_names,
    kpi_summary,
    load_pipeline_data,
    load_uploaded_data,
    numeric_columns,
    prepare_time_series,
    profile_dataframe,
    serialisable_config,
    trend_summary,
)
from reporting import create_excel_report, create_html_report


BLUE = "#1857C9"
BLUE_DARK = "#0F3F9C"
RED = "#CF3430"
TEXT = "#152238"
MUTED = "#48597A"
PLOT_COLORS = [BLUE, "#2F7DE1", "#6B8FD8", "#E8964A", RED, "#3B9B7A"]

st.set_page_config(
    page_title="Operational Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
<style>
:root{{--oi-blue:{BLUE};--oi-red:{RED};--oi-text:{TEXT};--oi-muted:{MUTED};}}
.stApp{{background:#eef3fa;color:{TEXT}}}
[data-testid="stHeader"]{{background:rgba(238,243,250,.88)}}
[data-testid="stSidebar"]{{background:#f7f9fd;border-right:1px solid #c9d8ec}}
.oi-hero{{background:linear-gradient(135deg,{BLUE_DARK},{BLUE});color:white;padding:1.35rem 1.55rem;border-radius:12px;margin-bottom:1rem;box-shadow:0 8px 24px rgba(15,63,156,.15)}}
.oi-hero h1{{margin:0;font-size:1.9rem}} .oi-hero p{{margin:.35rem 0 0;opacity:.9}}
.oi-panel{{background:#fff;border:1px solid #c9d8ec;border-radius:10px;padding:1rem 1.1rem;margin:.4rem 0 1rem}}
.oi-badge{{display:inline-block;padding:.2rem .55rem;border-radius:999px;background:#e4ecf9;color:{BLUE_DARK};font-size:.78rem;font-weight:700}}
.oi-good{{color:{BLUE};font-weight:700}} .oi-bad{{color:{RED};font-weight:700}}
div[data-testid="stMetric"]{{background:#fff;border:1px solid #c9d8ec;padding:.85rem;border-radius:10px}}
.stButton>button,.stDownloadButton>button{{border-radius:8px;border:1px solid {BLUE};}}
.stButton>button[kind="primary"]{{background:{BLUE};color:#fff}}
[data-testid="stDataFrame"]{{background:#fff;border:1px solid #dce7f4;border-radius:8px}}
</style>
""",
    unsafe_allow_html=True,
)


def default_config() -> dict[str, Any]:
    return {
        "app_title": "Operational Intelligence",
        "template": "Executive",
        "date_column": "",
        "dashboard_metrics": [],
        "chart_metrics": [],
        "analysis_metrics": [],
        "table_columns": [],
        "dimensions": [],
        "formulas": [],
        "rules": [],
        "anomaly_window": 7,
    }


def initialise_state() -> None:
    defaults = {
        "raw_df": None,
        "data": None,
        "source_name": "No source synced",
        "last_sync": None,
        "config": default_config(),
        "presets": {},
        "formula_errors": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


initialise_state()


def configure_for_frame(frame: pd.DataFrame, source_name: str) -> None:
    config = st.session_state.config
    st.session_state.raw_df = frame.copy()
    transformed, errors = apply_calculated_fields(frame, config.get("formulas", []))
    st.session_state.data = transformed
    st.session_state.formula_errors = errors
    st.session_state.source_name = source_name
    st.session_state.last_sync = datetime.now(timezone.utc).isoformat(timespec="seconds")

    numbers = numeric_columns(transformed)
    dates = detect_date_columns(transformed)
    if config.get("date_column") not in transformed.columns:
        config["date_column"] = dates[0] if dates else ""
    if not config.get("dashboard_metrics"):
        config["dashboard_metrics"] = numbers[:6]
    if not config.get("chart_metrics"):
        config["chart_metrics"] = numbers[:4]
    if not config.get("analysis_metrics"):
        config["analysis_metrics"] = numbers[:6]
    if not config.get("table_columns"):
        config["table_columns"] = list(transformed.columns[:12])


def rebuild_transformed_data() -> None:
    if isinstance(st.session_state.raw_df, pd.DataFrame):
        transformed, errors = apply_calculated_fields(
            st.session_state.raw_df, st.session_state.config.get("formulas", [])
        )
        st.session_state.data = transformed
        st.session_state.formula_errors = errors


def get_data() -> pd.DataFrame | None:
    data = st.session_state.data
    return data if isinstance(data, pd.DataFrame) else None


def no_data_message() -> None:
    st.info("Sync an Excel/CSV file or pipeline on **Data & configuration** to activate this page.")


def fmt_number(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def plot_time_series(data: pd.DataFrame, date_column: str, metrics: list[str], title: str) -> go.Figure:
    series = prepare_time_series(data, date_column, metrics)
    if series.empty:
        return go.Figure().update_layout(title="No valid date series")
    fig = px.line(
        series,
        x=date_column,
        y=[metric for metric in metrics if metric in series.columns],
        title=title,
        color_discrete_sequence=PLOT_COLORS,
    )
    fig.update_layout(
        template="plotly_white",
        legend_title_text="",
        hovermode="x unified",
        margin=dict(l=20, r=20, t=55, b=20),
    )
    return fig


def filtered_dashboard_data(data: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    result = data.copy()
    date_column = config.get("date_column", "")
    with st.expander("Dashboard filters", expanded=False):
        if date_column in result.columns:
            parsed = pd.to_datetime(result[date_column], errors="coerce")
            valid = parsed.dropna()
            if not valid.empty:
                start, end = st.date_input(
                    "Date range",
                    value=(valid.min().date(), valid.max().date()),
                    min_value=valid.min().date(),
                    max_value=valid.max().date(),
                    key="dashboard_date_range",
                )
                mask = parsed.dt.date.between(start, end)
                result = result[mask]
        for dimension in config.get("dimensions", [])[:3]:
            if dimension not in result.columns:
                continue
            values = result[dimension].dropna().astype(str).drop_duplicates().tolist()
            selected = st.multiselect(
                f"{dimension}", values, default=values, key=f"filter_{dimension}"
            )
            if selected:
                result = result[result[dimension].astype(str).isin(selected)]
    return result


config = st.session_state.config
st.markdown(
    f"""<div class="oi-hero"><h1>The ultimate operational intelligence</h1>
<p>Data profiling, configurable KPIs, dashboards, comparative analysis, trends, estimation and repeatable reports.</p></div>""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Operational Intelligence")
    st.caption("Practical analytics for repeat operations work")
    if get_data() is None:
        st.warning("No data synced")
    else:
        st.success(f"{len(get_data()):,} rows synced")
        st.caption(st.session_state.source_name)
    if st.session_state.last_sync:
        st.caption(f"Last sync: {st.session_state.last_sync}")
    st.divider()
    st.markdown("**Workflow**")
    st.markdown("1. Sync data\n2. Review quality\n3. Add formulas and KPI rules\n4. Choose dashboard/analysis content\n5. Download report or preset")

page_data, page_dashboard, page_analysis, page_report = st.tabs(
    ["Data & configuration", "Dashboard", "Analysis", "Report download"]
)

with page_data:
    source_tab, profile_tab, formula_tab, layout_tab, preset_tab = st.tabs(
        ["Data source", "Data profile", "Formulas & KPI rules", "Dashboard & analysis", "Presets"]
    )

    with source_tab:
        st.subheader("Connect and sync")
        source_mode = st.radio("Source", ["Upload file", "HTTP data pipeline"], horizontal=True)
        if source_mode == "Upload file":
            upload = st.file_uploader("Excel or CSV", type=["xlsx", "xlsm", "csv"])
            selected_sheet: str | int = 0
            separator = ","
            encoding = "utf-8"
            if upload is not None and upload.name.lower().endswith((".xlsx", ".xlsm")):
                try:
                    sheets = excel_sheet_names(upload.getvalue())
                    selected_sheet = st.selectbox("Excel sheet", sheets)
                except DataLoadError as exc:
                    st.error(str(exc))
            elif upload is not None:
                c1, c2 = st.columns(2)
                separator = c1.selectbox("CSV separator", [",", ";", "\t", "|"], format_func=lambda x: "Tab" if x == "\t" else x)
                encoding = c2.selectbox("CSV encoding", ["utf-8", "utf-8-sig", "latin-1"])
            if st.button("Sync uploaded data", type="primary", disabled=upload is None):
                try:
                    frame = load_uploaded_data(
                        upload.getvalue(), upload.name, selected_sheet, separator, encoding
                    )
                    configure_for_frame(frame, upload.name)
                    st.success(f"Synced {len(frame):,} rows and {len(frame.columns):,} headers.")
                except DataLoadError as exc:
                    st.error(str(exc))
        else:
            st.caption("The endpoint must return CSV or a JSON array (or an object containing data/items/results/value).")
            pipeline_url = st.text_input("Pipeline URL", placeholder="https://example.com/export/latest.csv")
            c1, c2 = st.columns(2)
            pipeline_format = c1.selectbox("Response format", ["Auto", "CSV", "JSON"])
            timeout = c2.number_input("Timeout (seconds)", 5, 120, 30)
            token = st.text_input("Bearer token (optional)", type="password")
            if st.button("Sync pipeline", type="primary", disabled=not pipeline_url):
                try:
                    frame = load_pipeline_data(pipeline_url, pipeline_format, token, int(timeout))
                    configure_for_frame(frame, pipeline_url)
                    st.success(f"Synced {len(frame):,} rows and {len(frame.columns):,} headers.")
                except DataLoadError as exc:
                    st.error(str(exc))

        data = get_data()
        if data is not None:
            st.markdown("#### Preview")
            st.dataframe(data.head(100), use_container_width=True, hide_index=True)

    with profile_tab:
        data = get_data()
        if data is None:
            no_data_message()
        else:
            profile = profile_dataframe(data)
            missing = int(data.isna().sum().sum())
            duplicates = int(data.duplicated().sum())
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Rows", f"{len(data):,}")
            c2.metric("Headers", f"{len(data.columns):,}")
            c3.metric("Missing cells", f"{missing:,}")
            c4.metric("Duplicate rows", f"{duplicates:,}")
            st.dataframe(
                profile,
                use_container_width=True,
                hide_index=True,
                column_config={"Missing %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%")},
            )
            quality_csv = profile.to_csv(index=False).encode("utf-8")
            st.download_button("Download data profile (CSV)", quality_csv, "data_profile.csv", "text/csv")

    with formula_tab:
        data = get_data()
        if data is None:
            no_data_message()
        else:
            st.subheader("Calculated fields")
            st.caption("Use headers in square brackets, for example: ([Output] / [Run Hours]) / 1000. Allowed functions: abs, sqrt, log, log10, exp, round, minimum, maximum and where.")
            c1, c2 = st.columns([1, 2])
            formula_name = c1.text_input("New header name", key="formula_name")
            formula_expression = c2.text_input("Formula", key="formula_expression", placeholder="[Actual] / [Target] * 100")
            if st.button("Add calculated field", disabled=not (formula_name and formula_expression)):
                try:
                    from core import evaluate_formula

                    evaluate_formula(data, formula_expression)
                    formulas = config.setdefault("formulas", [])
                    formulas[:] = [item for item in formulas if item.get("name") != formula_name]
                    formulas.append({"name": formula_name.strip(), "expression": formula_expression.strip()})
                    rebuild_transformed_data()
                    st.success(f"Calculated field '{formula_name}' added.")
                    st.rerun()
                except FormulaError as exc:
                    st.error(str(exc))
            if config.get("formulas"):
                formula_frame = pd.DataFrame(config["formulas"])
                st.dataframe(formula_frame, use_container_width=True, hide_index=True)
                remove_formula = st.selectbox("Remove calculated field", [""] + [x["name"] for x in config["formulas"]])
                if st.button("Remove formula", disabled=not remove_formula):
                    config["formulas"] = [x for x in config["formulas"] if x["name"] != remove_formula]
                    rebuild_transformed_data()
                    st.rerun()
            for error in st.session_state.formula_errors:
                st.warning(error)

            st.divider()
            st.subheader("KPI high/low/target rules")
            data = get_data()
            numbers = numeric_columns(data)
            if not numbers:
                st.warning("No numeric headers are available for KPI rules.")
            else:
                r1, r2, r3 = st.columns(3)
                metric = r1.selectbox("KPI header", numbers, key="rule_metric")
                aggregation = r2.selectbox("Aggregation", ["Latest", "Average", "Sum", "Minimum", "Maximum", "Median"], key="rule_agg")
                direction = r3.selectbox("Interpretation", ["Higher is better", "Lower is better", "Between low and high"], key="rule_direction")
                threshold_mode = st.radio("Threshold source", ["Constant", "Header"], horizontal=True, key="rule_mode")
                rule: dict[str, Any] = {
                    "metric": metric,
                    "aggregation": aggregation,
                    "direction": direction,
                    "threshold_mode": threshold_mode,
                }
                if direction == "Between low and high":
                    c1, c2 = st.columns(2)
                    if threshold_mode == "Constant":
                        rule["low"] = c1.number_input("Low value", value=0.0, key="rule_low")
                        rule["high"] = c2.number_input("High value", value=100.0, key="rule_high")
                    else:
                        rule["low_column"] = c1.selectbox("Low-value header", numbers, key="rule_low_col")
                        rule["high_column"] = c2.selectbox("High-value header", numbers, key="rule_high_col")
                else:
                    if threshold_mode == "Constant":
                        rule["target"] = st.number_input("Target value", value=0.0, key="rule_target")
                    else:
                        rule["target_column"] = st.selectbox("Reference header", numbers, key="rule_target_col")
                if st.button("Save KPI rule", type="primary"):
                    rules = config.setdefault("rules", [])
                    rules[:] = [item for item in rules if item.get("metric") != metric]
                    rules.append(rule)
                    if metric not in config.setdefault("dashboard_metrics", []):
                        config["dashboard_metrics"].append(metric)
                    st.success(f"Rule saved for {metric}.")
                    st.rerun()
                if config.get("rules"):
                    st.dataframe(pd.DataFrame(config["rules"]), use_container_width=True, hide_index=True)
                    remove_rule = st.selectbox("Remove KPI rule", [""] + [x["metric"] for x in config["rules"]])
                    if st.button("Remove KPI rule", disabled=not remove_rule):
                        config["rules"] = [x for x in config["rules"] if x["metric"] != remove_rule]
                        st.rerun()

    with layout_tab:
        data = get_data()
        if data is None:
            no_data_message()
        else:
            numbers = numeric_columns(data)
            dates = detect_date_columns(data)
            categories = [
                column for column in data.columns
                if column not in numbers and data[column].nunique(dropna=True) <= 100
            ]
            st.subheader("Choose what users see")
            config["app_title"] = st.text_input("Dashboard title", config.get("app_title", "Operational Intelligence"))
            config["template"] = st.selectbox(
                "Default dashboard template",
                ["Executive", "Operations", "Dense", "Minimal"],
                index=["Executive", "Operations", "Dense", "Minimal"].index(config.get("template", "Executive")),
            )
            date_options = [""] + list(data.columns)
            current_date = config.get("date_column", "")
            config["date_column"] = st.selectbox(
                "Date/time header",
                date_options,
                index=date_options.index(current_date) if current_date in date_options else 0,
                help=f"Detected date candidates: {', '.join(dates) if dates else 'none'}",
            )
            config["dashboard_metrics"] = st.multiselect(
                "Dashboard KPI cards",
                numbers,
                default=[x for x in config.get("dashboard_metrics", []) if x in numbers],
            )
            config["chart_metrics"] = st.multiselect(
                "Dashboard charts",
                numbers,
                default=[x for x in config.get("chart_metrics", []) if x in numbers],
            )
            config["analysis_metrics"] = st.multiselect(
                "Analysis/trend metrics",
                numbers,
                default=[x for x in config.get("analysis_metrics", []) if x in numbers],
            )
            config["table_columns"] = st.multiselect(
                "Analysis table columns",
                list(data.columns),
                default=[x for x in config.get("table_columns", []) if x in data.columns],
            )
            config["dimensions"] = st.multiselect(
                "Dashboard filter headers",
                categories,
                default=[x for x in config.get("dimensions", []) if x in categories],
                max_selections=3,
            )
            config["anomaly_window"] = st.slider("Trend/anomaly rolling window", 3, 90, int(config.get("anomaly_window", 7)))
            st.success("Configuration is saved in this browser session. Use Presets to download it for repeat work.")

    with preset_tab:
        st.subheader("Save, download and upload presets")
        st.caption("A preset stores formulas, KPI rules and page selections. It intentionally excludes source data and secrets.")
        preset_name = st.text_input("Preset name", value="operational-intelligence-preset")
        if st.button("Save preset in this session", disabled=not preset_name):
            st.session_state.presets[preset_name] = serialisable_config(config)
            st.success(f"Saved '{preset_name}'.")
        current_json = json.dumps(serialisable_config(config), indent=2).encode("utf-8")
        st.download_button(
            "Download current preset",
            current_json,
            f"{preset_name or 'operational-intelligence-preset'}.json",
            "application/json",
        )
        if st.session_state.presets:
            saved = st.selectbox("Session presets", list(st.session_state.presets))
            c1, c2 = st.columns(2)
            if c1.button("Load session preset"):
                st.session_state.config = serialisable_config(st.session_state.presets[saved])
                rebuild_transformed_data()
                st.rerun()
            saved_json = json.dumps(st.session_state.presets[saved], indent=2).encode("utf-8")
            c2.download_button("Download selected preset", saved_json, f"{saved}.json", "application/json")
        uploaded_preset = st.file_uploader("Upload preset JSON", type=["json"], key="preset_upload")
        if st.button("Apply uploaded preset", disabled=uploaded_preset is None):
            try:
                candidate = json.loads(uploaded_preset.getvalue().decode("utf-8"))
                if not isinstance(candidate, dict):
                    raise ValueError("Preset root must be a JSON object.")
                merged = default_config()
                merged.update(candidate)
                st.session_state.config = merged
                rebuild_transformed_data()
                st.success("Preset applied.")
                st.rerun()
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                st.error(f"Invalid preset: {exc}")

with page_dashboard:
    data = get_data()
    if data is None:
        no_data_message()
    else:
        config = st.session_state.config
        h1, h2 = st.columns([3, 1])
        h1.subheader(config.get("app_title", "Operational Intelligence"))
        templates = ["Executive", "Operations", "Dense", "Minimal"]
        selected_template = h2.selectbox(
            "View template",
            templates,
            index=templates.index(config.get("template", "Executive")),
            key="dashboard_template_selector",
        )
        config["template"] = selected_template
        view = filtered_dashboard_data(data, config)
        st.caption(f"{len(view):,} of {len(data):,} rows in view · {st.session_state.source_name}")
        metrics = [x for x in config.get("dashboard_metrics", []) if x in view.columns]
        summary = kpi_summary(view, metrics, config.get("rules", []))
        if metrics:
            columns_per_row = 3 if selected_template in {"Executive", "Minimal"} else 4
            visible = metrics[:4] if selected_template == "Minimal" else metrics
            for start in range(0, len(visible), columns_per_row):
                columns = st.columns(columns_per_row)
                for target, (_, row) in zip(columns, summary.iloc[start : start + columns_per_row].iterrows()):
                    delta = None if pd.isna(row.get("Variance %")) else f"{row['Variance %']:+.1f}% vs reference"
                    direction = row.get("Direction")
                    delta_color = "inverse" if direction == "Lower is better" else "normal"
                    target.metric(row["Metric"], fmt_number(row["Value"]), delta, delta_color=delta_color)
        else:
            st.info("Select dashboard KPI cards on Data & configuration.")

        date_column = config.get("date_column", "")
        chart_metrics = [x for x in config.get("chart_metrics", []) if x in view.columns]
        if date_column in view.columns and chart_metrics:
            if selected_template == "Operations" and len(chart_metrics) > 1:
                left, right = st.columns(2)
                left.plotly_chart(plot_time_series(view, date_column, chart_metrics[::2], "Operational trend A"), use_container_width=True)
                right.plotly_chart(plot_time_series(view, date_column, chart_metrics[1::2], "Operational trend B"), use_container_width=True)
            else:
                st.plotly_chart(plot_time_series(view, date_column, chart_metrics, "Performance over time"), use_container_width=True)
        elif chart_metrics:
            st.warning("Choose a valid date/time header to draw dashboard trends.")

        if selected_template in {"Executive", "Dense"} and not summary.empty:
            st.markdown("#### KPI status")
            st.dataframe(summary, use_container_width=True, hide_index=True)
        if selected_template in {"Operations", "Dense"}:
            st.markdown("#### Latest operational records")
            columns = [x for x in config.get("table_columns", []) if x in view.columns]
            st.dataframe(view[columns or list(view.columns)].tail(50), use_container_width=True, hide_index=True)

with page_analysis:
    data = get_data()
    if data is None:
        no_data_message()
    else:
        config = st.session_state.config
        compare_tab, trend_tab, table_tab, ai_tab = st.tabs(
            ["Compare charts", "Trends & estimation", "Data tables", "Copilot Analyst AI"]
        )
        numbers = numeric_columns(data)
        selected_metrics = [x for x in config.get("analysis_metrics", []) if x in numbers] or numbers[:6]
        date_column = config.get("date_column", "")

        with compare_tab:
            st.subheader("Side-by-side chart comparison")
            x_options = list(data.columns)
            default_x = x_options.index(date_column) if date_column in x_options else 0
            x_axis = st.selectbox("X-axis", x_options, index=default_x, key="compare_x")
            c1, c2 = st.columns(2)
            with c1:
                metric_a = st.selectbox("Chart A metric", numbers, key="metric_a")
                type_a = st.selectbox("Chart A type", ["Line", "Bar", "Scatter"], key="type_a")
            with c2:
                metric_b = st.selectbox("Chart B metric", numbers, index=min(1, max(0, len(numbers) - 1)), key="metric_b")
                type_b = st.selectbox("Chart B type", ["Line", "Bar", "Scatter"], key="type_b")
            left, right = st.columns(2)
            chart_data = data.copy()
            if x_axis == date_column:
                chart_data[x_axis] = pd.to_datetime(chart_data[x_axis], errors="coerce")
                chart_data = chart_data.sort_values(x_axis)
            chart_functions = {"Line": px.line, "Bar": px.bar, "Scatter": px.scatter}
            for container, metric, chart_type in [(left, metric_a, type_a), (right, metric_b, type_b)]:
                fig = chart_functions[chart_type](chart_data, x=x_axis, y=metric, color_discrete_sequence=[BLUE])
                fig.update_layout(template="plotly_white", margin=dict(l=10, r=10, t=30, b=10))
                container.plotly_chart(fig, use_container_width=True)

        with trend_tab:
            st.subheader("Trend spotting, anomalies and next-point estimation")
            if date_column not in data.columns:
                st.warning("Set a date/time header on Data & configuration to activate trend analysis.")
            elif not selected_metrics:
                st.warning("Select analysis metrics on Data & configuration.")
            else:
                trends = trend_summary(data, date_column, selected_metrics, int(config.get("anomaly_window", 7)))
                st.dataframe(
                    trends,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Latest anomaly": st.column_config.CheckboxColumn()},
                )
                st.plotly_chart(plot_time_series(data, date_column, selected_metrics, "Selected analysis trends"), use_container_width=True)
                if len(selected_metrics) >= 2:
                    corr = data[selected_metrics].apply(pd.to_numeric, errors="coerce").corr()
                    fig = px.imshow(
                        corr,
                        text_auto=".2f",
                        color_continuous_scale=[[0, RED], [0.5, "#FFFFFF"], [1, BLUE]],
                        zmin=-1,
                        zmax=1,
                        title="Correlation matrix",
                    )
                    st.plotly_chart(fig, use_container_width=True)
                st.caption("Estimates use a simple linear slope. Anomalies use the latest rolling z-score. Treat both as screening signals, not causal proof.")

        with table_tab:
            st.subheader("Configured data tables")
            table_columns = [x for x in config.get("table_columns", []) if x in data.columns]
            selected_table_columns = st.multiselect(
                "Visible columns",
                list(data.columns),
                default=table_columns or list(data.columns[:12]),
                key="analysis_table_columns_runtime",
            )
            search = st.text_input("Filter rows containing text", key="table_search")
            table = data[selected_table_columns] if selected_table_columns else data
            if search:
                mask = table.astype(str).apply(lambda column: column.str.contains(search, case=False, na=False)).any(axis=1)
                table = table[mask]
            st.dataframe(table, use_container_width=True, hide_index=True, height=520)
            st.download_button("Download displayed table (CSV)", table.to_csv(index=False).encode("utf-8"), "analysis_table.csv", "text/csv")

        with ai_tab:
            st.subheader("Copilot Analyst AI")
            st.caption("A local brief always works. Optional remote connectors support an Azure OpenAI deployment or an authenticated Copilot/agent HTTP endpoint. Secrets are not stored in presets.")
            profile = profile_dataframe(data)
            kpis = kpi_summary(data, [x for x in config.get("dashboard_metrics", []) if x in data.columns], config.get("rules", []))
            trends = trend_summary(data, date_column, selected_metrics, int(config.get("anomaly_window", 7))) if date_column in data.columns else pd.DataFrame()
            st.markdown(local_analyst_brief(kpis, trends))
            mode_label = st.selectbox("Analyst connector", ["Local brief only", "Azure OpenAI", "Copilot agent HTTP endpoint"])
            settings: dict[str, Any] = {"mode": "local"}
            if mode_label == "Azure OpenAI":
                settings = {
                    "mode": "azure_openai",
                    "endpoint": st.text_input("Azure OpenAI endpoint", key="ai_endpoint"),
                    "deployment": st.text_input("Deployment name", key="ai_deployment"),
                    "api_version": st.text_input("API version", value="2024-10-21", key="ai_version"),
                    "api_key": st.text_input("API key", type="password", key="ai_key"),
                }
            elif mode_label == "Copilot agent HTTP endpoint":
                settings = {
                    "mode": "copilot_http",
                    "endpoint": st.text_input("Agent endpoint", key="copilot_endpoint"),
                    "bearer_token": st.text_input("Bearer token", type="password", key="copilot_token"),
                }
            question = st.text_area("Question", placeholder="Which metrics need immediate attention, and what should the operations team verify first?")
            include_sample = st.checkbox("Include up to 20 sample rows in remote AI context", value=False)
            confirm_remote = st.checkbox("I understand that the compact context will be sent to the configured endpoint", value=False)
            if st.button("Ask Analyst AI", disabled=mode_label == "Local brief only" or not question):
                if not confirm_remote:
                    st.error("Confirm remote data sharing before sending the request.")
                else:
                    context = compact_context(profile, kpis, trends, data if include_sample else data.iloc[0:0])
                    try:
                        with st.spinner("Analysing the configured operational context..."):
                            answer = ask_analyst_agent(question, context, settings)
                        st.markdown(answer)
                    except AnalystConnectorError as exc:
                        st.error(str(exc))

with page_report:
    data = get_data()
    if data is None:
        no_data_message()
    else:
        config = st.session_state.config
        st.subheader("Build and download a repeatable report")
        c1, c2 = st.columns(2)
        report_title = c1.text_input("Report title", value=f"{config.get('app_title', 'Operational Intelligence')} Report")
        report_author = c2.text_input("Prepared for/by", value="John Akukuata")
        report_notes = st.text_area("Report note", placeholder="Reporting period, operational context, assumptions or sign-off note")
        include_profile = st.checkbox("Include data profile", value=True)
        include_data = st.checkbox("Include configured data table", value=True)
        metrics = [x for x in config.get("dashboard_metrics", []) if x in data.columns]
        analysis_metrics = [x for x in config.get("analysis_metrics", []) if x in data.columns]
        profile = profile_dataframe(data)
        kpis = kpi_summary(data, metrics, config.get("rules", []))
        date_column = config.get("date_column", "")
        trends = trend_summary(data, date_column, analysis_metrics, int(config.get("anomaly_window", 7))) if date_column in data.columns else pd.DataFrame()
        table_columns = [x for x in config.get("table_columns", []) if x in data.columns]
        report_data = data[table_columns] if table_columns else data
        html_report = create_html_report(
            report_title,
            report_author,
            report_notes,
            st.session_state.source_name,
            profile,
            kpis,
            trends,
            report_data,
            serialisable_config(config),
            include_profile,
            include_data,
        )
        excel_report = create_excel_report(
            report_data, profile, kpis, trends, serialisable_config(config)
        )
        preset_json = json.dumps(serialisable_config(config), indent=2).encode("utf-8")
        transformed_csv = data.to_csv(index=False).encode("utf-8")
        c1, c2, c3, c4 = st.columns(4)
        c1.download_button("HTML report", html_report, "operational_intelligence_report.html", "text/html", use_container_width=True)
        c2.download_button("Excel report", excel_report, "operational_intelligence_report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        c3.download_button("Transformed CSV", transformed_csv, "operational_intelligence_data.csv", "text/csv", use_container_width=True)
        c4.download_button("Preset JSON", preset_json, "operational_intelligence_preset.json", "application/json", use_container_width=True)
        st.markdown("#### Report preview")
        st.dataframe(kpis, use_container_width=True, hide_index=True)
        st.dataframe(trends, use_container_width=True, hide_index=True)
