from __future__ import annotations

import ast
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests


class DataLoadError(ValueError):
    """Raised when a source cannot be converted into a dataframe."""


class FormulaError(ValueError):
    """Raised when a calculated-field expression is invalid or unsafe."""


def make_unique_columns(columns: Iterable[Any]) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []
    for raw in columns:
        base = str(raw).strip() or "Unnamed"
        count = seen.get(base, 0)
        output.append(base if count == 0 else f"{base}_{count + 1}")
        seen[base] = count + 1
    return output


def normalise_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result.columns = make_unique_columns(result.columns)
    result = result.dropna(how="all").reset_index(drop=True)
    for column in result.select_dtypes(include="object").columns:
        result[column] = result[column].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )
    return result


def excel_sheet_names(payload: bytes) -> list[str]:
    try:
        return pd.ExcelFile(io.BytesIO(payload), engine="openpyxl").sheet_names
    except Exception as exc:  # pragma: no cover - engine error details vary
        raise DataLoadError(f"Could not inspect the Excel workbook: {exc}") from exc


def load_uploaded_data(
    payload: bytes,
    filename: str,
    sheet_name: str | int | None = 0,
    csv_separator: str = ",",
    csv_encoding: str = "utf-8",
) -> pd.DataFrame:
    suffix = filename.lower().rsplit(".", 1)[-1]
    try:
        if suffix in {"xlsx", "xlsm"}:
            frame = pd.read_excel(
                io.BytesIO(payload), sheet_name=sheet_name, engine="openpyxl"
            )
        elif suffix == "csv":
            frame = pd.read_csv(
                io.BytesIO(payload), sep=csv_separator or ",", encoding=csv_encoding
            )
        else:
            raise DataLoadError("Supported uploads are .csv, .xlsx and .xlsm files.")
    except DataLoadError:
        raise
    except Exception as exc:
        raise DataLoadError(f"Could not read {filename}: {exc}") from exc
    if not isinstance(frame, pd.DataFrame):
        raise DataLoadError("The selected source did not produce a table.")
    return normalise_dataframe(frame)


def load_pipeline_data(
    url: str,
    data_format: str = "Auto",
    bearer_token: str = "",
    timeout_seconds: int = 30,
) -> pd.DataFrame:
    if not url.lower().startswith(("http://", "https://")):
        raise DataLoadError("Pipeline URL must start with http:// or https://.")
    headers = {"Accept": "application/json, text/csv, application/octet-stream"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    try:
        response = requests.get(url, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DataLoadError(f"Pipeline sync failed: {exc}") from exc

    selected = data_format.lower()
    content_type = response.headers.get("content-type", "").lower()
    if selected == "auto":
        selected = "json" if "json" in content_type else "csv"
    try:
        if selected == "json":
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("data", "items", "results", "value"):
                    if isinstance(payload.get(key), list):
                        payload = payload[key]
                        break
            frame = pd.json_normalize(payload)
        elif selected == "csv":
            frame = pd.read_csv(io.BytesIO(response.content))
        else:
            raise DataLoadError("Pipeline format must be Auto, CSV or JSON.")
    except DataLoadError:
        raise
    except Exception as exc:
        raise DataLoadError(f"Pipeline response could not be parsed: {exc}") from exc
    return normalise_dataframe(frame)


def logical_type(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "Date/time"
    if pd.api.types.is_bool_dtype(series):
        return "Boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "Number"
    non_null = series.dropna()
    if non_null.empty:
        return "Empty"
    sample = non_null.astype(str).head(100)
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    if parsed.notna().mean() >= 0.85:
        return "Possible date/time"
    ratio = pd.to_numeric(sample.str.replace(",", "", regex=False), errors="coerce").notna().mean()
    if ratio >= 0.85:
        return "Possible number"
    if series.nunique(dropna=True) <= min(30, max(3, int(len(series) * 0.05))):
        return "Category"
    return "Text"


def profile_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    row_count = len(df)
    for column in df.columns:
        series = df[column]
        numeric = pd.to_numeric(series, errors="coerce")
        is_numeric = pd.api.types.is_numeric_dtype(series) or numeric.notna().mean() >= 0.85
        minimum: Any = numeric.min() if is_numeric and numeric.notna().any() else None
        maximum: Any = numeric.max() if is_numeric and numeric.notna().any() else None
        mean: Any = numeric.mean() if is_numeric and numeric.notna().any() else None
        examples = ", ".join(series.dropna().astype(str).drop_duplicates().head(3).tolist())
        missing = int(series.isna().sum())
        rows.append(
            {
                "Header": column,
                "Data type": str(series.dtype),
                "Logical type": logical_type(series),
                "Rows": row_count,
                "Non-missing": int(series.notna().sum()),
                "Missing": missing,
                "Missing %": round((missing / row_count * 100) if row_count else 0, 2),
                "Unique": int(series.nunique(dropna=True)),
                "Min": minimum,
                "Max": maximum,
                "Mean": mean,
                "Examples": examples,
            }
        )
    return pd.DataFrame(rows)


def detect_date_columns(df: pd.DataFrame) -> list[str]:
    results: list[str] = []
    for column in df.columns:
        series = df[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            results.append(column)
            continue
        name_hint = bool(re.search(r"date|time|day|month|year|period", column, re.I))
        if name_hint and len(series.dropna()):
            parsed = pd.to_datetime(series.dropna().head(250), errors="coerce")
            if parsed.notna().mean() >= 0.7:
                results.append(column)
    return results


def numeric_columns(df: pd.DataFrame) -> list[str]:
    output: list[str] = []
    for column in df.columns:
        series = df[column]
        if pd.api.types.is_numeric_dtype(series):
            output.append(column)
        elif len(series.dropna()):
            parsed = pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")
            if parsed.notna().mean() >= 0.85:
                output.append(column)
    return output


_PLACEHOLDER = re.compile(r"\[([^\[\]]+)\]")
_ALLOWED_BINARY = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}
_ALLOWED_COMPARE = {
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}
_ALLOWED_FUNCTIONS = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log": np.log,
    "log10": np.log10,
    "exp": np.exp,
    "round": np.round,
    "minimum": np.minimum,
    "maximum": np.maximum,
    "where": np.where,
}


def _evaluate_ast(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate_ast(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
        return _ALLOWED_BINARY[type(node.op)](
            _evaluate_ast(node.left, names), _evaluate_ast(node.right, names)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
        value = _evaluate_ast(node.operand, names)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.Not):
            return ~value if hasattr(value, "index") else not value
        return value
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
        op = type(node.ops[0])
        if op in _ALLOWED_COMPARE:
            return _ALLOWED_COMPARE[op](
                _evaluate_ast(node.left, names),
                _evaluate_ast(node.comparators[0], names),
            )
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        values = [_evaluate_ast(value, names) for value in node.values]
        result = values[0]
        for value in values[1:]:
            result = (result & value) if isinstance(node.op, ast.And) else (result | value)
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = _ALLOWED_FUNCTIONS.get(node.func.id)
        if function is None or node.keywords:
            raise FormulaError(f"Function '{node.func.id}' is not allowed.")
        return function(*[_evaluate_ast(arg, names) for arg in node.args])
    raise FormulaError("Expression contains an unsupported operation.")


def evaluate_formula(df: pd.DataFrame, expression: str) -> pd.Series:
    if not expression.strip():
        raise FormulaError("Enter a formula.")
    names: dict[str, Any] = {}
    replacements: dict[str, str] = {}
    for column in _PLACEHOLDER.findall(expression):
        if column not in df.columns:
            raise FormulaError(f"Unknown header: {column}")
        if column not in replacements:
            token = f"column_{len(replacements)}"
            replacements[column] = token
            names[token] = pd.to_numeric(df[column], errors="coerce")
    if not replacements:
        raise FormulaError("Reference at least one header using [Header name].")
    safe_expression = expression
    for column, token in replacements.items():
        safe_expression = safe_expression.replace(f"[{column}]", token)
    try:
        tree = ast.parse(safe_expression, mode="eval")
        result = _evaluate_ast(tree, names)
    except FormulaError:
        raise
    except Exception as exc:
        raise FormulaError(f"Formula could not be evaluated: {exc}") from exc
    if np.isscalar(result):
        return pd.Series([result] * len(df), index=df.index)
    result_series = pd.Series(result, index=df.index)
    return result_series.replace([np.inf, -np.inf], np.nan)


def apply_calculated_fields(df: pd.DataFrame, formulas: list[dict[str, str]]) -> tuple[pd.DataFrame, list[str]]:
    result = df.copy()
    errors: list[str] = []
    for item in formulas:
        name = str(item.get("name", "")).strip()
        expression = str(item.get("expression", "")).strip()
        if not name or not expression:
            continue
        try:
            result[name] = evaluate_formula(result, expression)
        except FormulaError as exc:
            errors.append(f"{name}: {exc}")
    return result, errors


def aggregate_value(series: pd.Series, method: str = "Latest") -> float | None:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    operations = {
        "Latest": lambda value: value.iloc[-1],
        "Average": lambda value: value.mean(),
        "Sum": lambda value: value.sum(),
        "Minimum": lambda value: value.min(),
        "Maximum": lambda value: value.max(),
        "Median": lambda value: value.median(),
    }
    return float(operations.get(method, operations["Latest"])(numeric))


def _rule_threshold(df: pd.DataFrame, rule: dict[str, Any], key: str) -> float | None:
    mode = rule.get("threshold_mode", "Constant")
    if mode == "Header":
        column = rule.get(f"{key}_column") or rule.get("target_column")
        if column in df.columns:
            return aggregate_value(df[column], rule.get("aggregation", "Latest"))
        return None
    raw = rule.get(key)
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def evaluate_rule(df: pd.DataFrame, rule: dict[str, Any]) -> dict[str, Any]:
    metric = rule.get("metric")
    aggregation = rule.get("aggregation", "Latest")
    value = aggregate_value(df[metric], aggregation) if metric in df.columns else None
    direction = rule.get("direction", "Higher is better")
    target = _rule_threshold(df, rule, "target")
    low = _rule_threshold(df, rule, "low")
    high = _rule_threshold(df, rule, "high")
    status = "No data" if value is None else "Neutral"
    variance = None
    reference = target
    if value is not None:
        if direction == "Higher is better" and target is not None:
            status = "On target" if value >= target else "Off target"
        elif direction == "Lower is better" and target is not None:
            status = "On target" if value <= target else "Off target"
        elif direction == "Between low and high" and low is not None and high is not None:
            status = "On target" if low <= value <= high else "Off target"
            reference = (low + high) / 2
        if reference not in (None, 0):
            variance = (value - reference) / abs(reference) * 100
    return {
        "Metric": metric,
        "Aggregation": aggregation,
        "Value": value,
        "Direction": direction,
        "Target": target,
        "Low": low,
        "High": high,
        "Status": status,
        "Variance %": variance,
    }


def kpi_summary(df: pd.DataFrame, metrics: list[str], rules: list[dict[str, Any]]) -> pd.DataFrame:
    rule_map = {item.get("metric"): item for item in rules}
    rows = []
    for metric in metrics:
        rule = rule_map.get(metric, {"metric": metric, "aggregation": "Latest"})
        rows.append(evaluate_rule(df, rule))
    return pd.DataFrame(rows)


def prepare_time_series(df: pd.DataFrame, date_column: str, metrics: list[str]) -> pd.DataFrame:
    if date_column not in df.columns:
        return pd.DataFrame()
    output = pd.DataFrame({date_column: pd.to_datetime(df[date_column], errors="coerce")})
    for metric in metrics:
        if metric in df.columns:
            output[metric] = pd.to_numeric(df[metric], errors="coerce")
    return output.dropna(subset=[date_column]).sort_values(date_column)


def trend_summary(
    df: pd.DataFrame,
    date_column: str,
    metrics: list[str],
    anomaly_window: int = 7,
) -> pd.DataFrame:
    data = prepare_time_series(df, date_column, metrics)
    rows: list[dict[str, Any]] = []
    for metric in metrics:
        if metric not in data.columns:
            continue
        series = data[[date_column, metric]].dropna()
        if series.empty:
            continue
        values = series[metric].astype(float)
        x = np.arange(len(values), dtype=float)
        slope = float(np.polyfit(x, values, 1)[0]) if len(values) >= 2 else 0.0
        first = float(values.iloc[0])
        latest = float(values.iloc[-1])
        change = ((latest - first) / abs(first) * 100) if first != 0 else None
        window = min(max(3, anomaly_window), len(values))
        baseline = values.rolling(window, min_periods=max(2, window // 2)).mean()
        spread = values.rolling(window, min_periods=max(2, window // 2)).std(ddof=0)
        z_score = ((values - baseline) / spread.replace(0, np.nan)).iloc[-1]
        anomaly = bool(pd.notna(z_score) and abs(float(z_score)) >= 2)
        forecast = latest + slope
        rows.append(
            {
                "Metric": metric,
                "First": first,
                "Latest": latest,
                "Change %": change,
                "Slope / row": slope,
                "Direction": "Rising" if slope > 0 else "Falling" if slope < 0 else "Flat",
                "Latest anomaly": anomaly,
                "Anomaly z-score": float(z_score) if pd.notna(z_score) else None,
                "Next-point estimate": forecast,
                "Observations": int(len(values)),
            }
        )
    return pd.DataFrame(rows)


def serialisable_config(config: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(config, default=str))
