from __future__ import annotations

import html
import io
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd


def _table(frame: pd.DataFrame, max_rows: int = 500) -> str:
    if frame.empty:
        return '<p class="muted">No data available for this section.</p>'
    safe = frame.head(max_rows).copy()
    return safe.to_html(index=False, border=0, classes="report-table", na_rep="—", escape=True)


def create_html_report(
    title: str,
    author: str,
    notes: str,
    source_name: str,
    profile: pd.DataFrame,
    kpis: pd.DataFrame,
    trends: pd.DataFrame,
    data: pd.DataFrame,
    config: dict[str, Any],
    include_profile: bool = True,
    include_data: bool = True,
) -> bytes:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = []
    sections.append(f"<h2>Executive KPI summary</h2>{_table(kpis)}")
    sections.append(f"<h2>Trend and estimation summary</h2>{_table(trends)}")
    if include_profile:
        sections.append(f"<h2>Data quality profile</h2>{_table(profile)}")
    if include_data:
        sections.append(f"<h2>Selected data</h2>{_table(data)}")
    config_json = html.escape(json.dumps(config, indent=2, default=str))
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#eef3fa;--panel:#fff;--border:#c9d8ec;--text:#152238;--muted:#48597a;--blue:#1857c9;--red:#cf3430}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px Arial,sans-serif}}
header{{background:linear-gradient(135deg,#0f3f9c,#1857c9);color:#fff;padding:28px 5vw}}
main{{max-width:1200px;margin:24px auto;padding:0 24px}} section{{background:#fff;border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:18px}}
h1{{margin:0 0 6px}} h2{{color:#0f3f9c;margin-top:0}} .meta{{opacity:.9}} .muted{{color:var(--muted)}}
.report-table{{width:100%;border-collapse:collapse;font-size:12px}} .report-table th{{background:#e4ecf9;color:#152238;text-align:left}}
.report-table th,.report-table td{{border:1px solid #dce7f4;padding:7px;vertical-align:top}} pre{{white-space:pre-wrap;background:#f2f6fc;padding:12px;border-radius:8px}}
@media print{{body{{background:#fff}} section{{break-inside:avoid}}}}
</style></head><body>
<header><h1>{html.escape(title)}</h1><div class="meta">Source: {html.escape(source_name)} · Prepared for {html.escape(author)} · {generated}</div></header>
<main><section><h2>Report note</h2><p>{html.escape(notes) if notes else 'No additional note.'}</p></section>
{''.join(f'<section>{section}</section>' for section in sections)}
<section><details><summary>Configuration used</summary><pre>{config_json}</pre></details></section>
</main></body></html>"""
    return document.encode("utf-8")


def create_excel_report(
    data: pd.DataFrame,
    profile: pd.DataFrame,
    kpis: pd.DataFrame,
    trends: pd.DataFrame,
    config: dict[str, Any],
) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm") as writer:
        data.to_excel(writer, sheet_name="Data", index=False)
        profile.to_excel(writer, sheet_name="Data Profile", index=False)
        kpis.to_excel(writer, sheet_name="KPI Summary", index=False)
        trends.to_excel(writer, sheet_name="Trends", index=False)
        config_frame = pd.DataFrame(
            [{"Configuration JSON": json.dumps(config, indent=2, default=str)}]
        )
        config_frame.to_excel(writer, sheet_name="Configuration", index=False)
        workbook = writer.book
        header = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1857C9", "border": 1}
        )
        warning = workbook.add_format({"font_color": "#CF3430", "bold": True})
        for sheet_name, frame in {
            "Data": data,
            "Data Profile": profile,
            "KPI Summary": kpis,
            "Trends": trends,
        }.items():
            sheet = writer.sheets[sheet_name]
            sheet.freeze_panes(1, 0)
            sheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
            for index, column in enumerate(frame.columns):
                width = min(max(len(str(column)) + 2, 12), 36)
                sheet.set_column(index, index, width)
                sheet.write(0, index, column, header)
            if sheet_name == "KPI Summary" and "Status" in frame.columns and len(frame):
                status_index = list(frame.columns).index("Status")
                sheet.conditional_format(
                    1,
                    status_index,
                    len(frame),
                    status_index,
                    {"type": "text", "criteria": "containing", "value": "Off target", "format": warning},
                )
        writer.sheets["Configuration"].set_column(0, 0, 120)
    return output.getvalue()
