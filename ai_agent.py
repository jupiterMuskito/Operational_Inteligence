from __future__ import annotations

import json
from typing import Any

import pandas as pd
import requests


class AnalystConnectorError(RuntimeError):
    pass


def compact_context(
    profile: pd.DataFrame,
    kpis: pd.DataFrame,
    trends: pd.DataFrame,
    sample: pd.DataFrame,
    max_sample_rows: int = 20,
) -> str:
    payload = {
        "data_profile": profile.head(100).fillna("").to_dict(orient="records"),
        "kpi_summary": kpis.fillna("").to_dict(orient="records"),
        "trend_summary": trends.fillna("").to_dict(orient="records"),
        "sample_rows": sample.head(max_sample_rows).fillna("").to_dict(orient="records"),
    }
    return json.dumps(payload, default=str, ensure_ascii=False)


def local_analyst_brief(kpis: pd.DataFrame, trends: pd.DataFrame) -> str:
    lines = ["### Operational intelligence brief"]
    if not kpis.empty:
        off = kpis[kpis["Status"] == "Off target"] if "Status" in kpis else pd.DataFrame()
        lines.append(f"- **KPIs reviewed:** {len(kpis)}; **off target:** {len(off)}.")
        for _, row in off.head(5).iterrows():
            value = row.get("Value")
            lines.append(f"- Attention: **{row.get('Metric')}** is {value:,.3f} ({row.get('Direction')}).")
    if not trends.empty:
        anomalies = trends[trends["Latest anomaly"] == True]  # noqa: E712
        lines.append(f"- **Trends reviewed:** {len(trends)}; **latest anomalies:** {len(anomalies)}.")
        for _, row in trends.head(5).iterrows():
            change = row.get("Change %")
            change_text = "n/a" if pd.isna(change) else f"{change:,.1f}%"
            lines.append(
                f"- {row.get('Metric')}: {row.get('Direction').lower()}, change {change_text}; "
                f"next-point estimate {row.get('Next-point estimate'):,.3f}."
            )
    lines.append("- Validate suggested causes with process owners; correlation and trend do not prove causation.")
    return "\n".join(lines)


def ask_analyst_agent(
    question: str,
    context: str,
    settings: dict[str, Any],
) -> str:
    mode = str(settings.get("mode", "disabled")).lower()
    if mode in {"", "disabled", "local"}:
        raise AnalystConnectorError("No remote Analyst AI connector is configured.")

    system_prompt = (
        "You are an operational data analyst. Use only the supplied dataset context. "
        "Separate observed facts from estimates, mention material missing data, quantify findings, "
        "and give practical actions. Do not invent causes or measurements."
    )
    timeout = int(settings.get("timeout_seconds", 60))

    if mode == "azure_openai":
        endpoint = str(settings.get("endpoint", "")).rstrip("/")
        deployment = str(settings.get("deployment", ""))
        api_version = str(settings.get("api_version", "2024-10-21"))
        api_key = str(settings.get("api_key", ""))
        if not all((endpoint, deployment, api_key)):
            raise AnalystConnectorError("Azure OpenAI endpoint, deployment and API key are required.")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        headers = {"Content-Type": "application/json", "api-key": api_key}
        body = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"DATA CONTEXT:\n{context}\n\nQUESTION:\n{question}"},
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            raise AnalystConnectorError(f"Azure OpenAI request failed: {exc}") from exc

    if mode == "copilot_http":
        endpoint = str(settings.get("endpoint", ""))
        token = str(settings.get("bearer_token", ""))
        if not endpoint:
            raise AnalystConnectorError("Copilot agent endpoint is required.")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"question": question, "context": context, "system_prompt": system_prompt}
        try:
            response = requests.post(endpoint, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            for key in ("answer", "response", "message", "output", "text"):
                if payload.get(key):
                    value = payload[key]
                    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            return json.dumps(payload, ensure_ascii=False)
        except (requests.RequestException, ValueError) as exc:
            raise AnalystConnectorError(f"Copilot agent request failed: {exc}") from exc

    raise AnalystConnectorError(f"Unknown Analyst AI mode: {mode}")
