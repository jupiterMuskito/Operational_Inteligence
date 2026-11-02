# Operational Intelligence

**The ultimate operational intelligence** — a practical Streamlit application for reusable operational dashboards, analysis, trends, estimation and report downloads.

The visual system is adapted from John Akukuata's coal-power-plant dashboard work: light blue-grey background, white bordered panels, deep operational blue for primary/status-positive signals, red for exceptions, compact KPI cards, target variance, period filtering, comparative trends and report tables.

## What the application does

- Uploads `.csv`, `.xlsx` or `.xlsm`, selects an Excel sheet, and syncs it into the app.
- Connects to an HTTP pipeline returning CSV or JSON, with optional bearer-token authentication.
- Profiles every header: source dtype, inferred logical type, row/non-missing/missing counts, missing percentage, unique count, numeric min/max/mean and examples.
- Adds safe calculated fields using readable expressions such as `[Output_MWh] / [Fuel_tons]`.
- Configures KPI aggregation and interpretation:
  - higher is better;
  - lower is better;
  - between low and high;
  - constant target/limits or values from other headers.
- Chooses dashboard KPI cards, trend charts, analysis metrics, table columns and up to three filter dimensions.
- Switches between Executive, Operations, Dense and Minimal dashboard templates.
- Compares two independent charts side by side.
- Spots rising/falling trends, latest rolling z-score anomalies, correlations and simple next-point estimates.
- Saves configuration in-session and downloads/uploads JSON presets for repeat work.
- Downloads a styled HTML report, formatted Excel report, transformed CSV and preset JSON.
- Includes a local deterministic analyst brief and optional remote Analyst AI connectors.

## Files

```text
operational_intelligence/
├── app.py                    # Streamlit user interface
├── core.py                   # loading, profiling, formulas, KPI rules and trends
├── ai_agent.py               # local brief + optional remote AI connectors
├── reporting.py              # HTML and Excel report builders
├── requirements.txt          # all runtime Python dependencies
├── runtime.txt               # Streamlit Cloud Python version
├── sample_data.csv           # demonstration data
├── sample_preset.json        # ready-to-upload configuration
├── .streamlit/config.toml    # visual theme and upload limit
├── .gitignore
└── README.md
```

## Run locally

Python 3.11 is recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit. Upload `sample_data.csv`, click **Sync uploaded data**, then upload `sample_preset.json` under **Data & configuration → Presets**.

## Deploy to Streamlit Community Cloud

1. Create a GitHub repository and place these files at its root (keep the `.streamlit` folder).
2. In Streamlit Community Cloud, select **Create app**.
3. Choose the repository and branch.
4. Set the main file path to `app.py`.
5. Select **Deploy**.
6. If you use a private pipeline or remote AI connector, add its credentials in the app's **Settings → Secrets**. Do not commit `secrets.toml`.

There are no system-package or compiled-model requirements. Every non-standard Python import used by the app is listed in `requirements.txt`, which directly addresses common `ModuleNotFoundError` deployment failures.

### If deployment fails

- Confirm `app.py` and `requirements.txt` are in the selected repository folder.
- Confirm the main file path is exactly `app.py`.
- Reboot the app after changing `requirements.txt`.
- Open **Manage app → Logs** and use the first traceback, not later cascading errors.
- Do not rename `core.py`, `ai_agent.py` or `reporting.py`; `app.py` imports those local modules by filename.
- Keep Python 3.11 in `runtime.txt` unless Streamlit Cloud announces a different supported format.

## Data pipeline contract

The pipeline connector performs an HTTP `GET`.

Supported responses:

- `text/csv` body; or
- a JSON array; or
- a JSON object containing an array under `data`, `items`, `results` or `value`.

An optional bearer token is sent as `Authorization: Bearer <token>`. The token exists only in the active app session.

For scheduled or enterprise refresh, put a small authenticated API in front of the warehouse/lakehouse and return a bounded analytical dataset. Keep write credentials out of this read-only dashboard.

## Formula syntax

Reference headers in square brackets:

```text
[Output_MWh] / [Fuel_tons]
([Actual] - [Target]) / abs([Target]) * 100
where([Run_Hours] > 0, [Output_MWh] / [Run_Hours], 0)
```

Allowed operations are arithmetic, comparisons and boolean combinations. Allowed functions are `abs`, `sqrt`, `log`, `log10`, `exp`, `round`, `minimum`, `maximum` and `where`. The app uses a restricted expression tree rather than Python `eval`.

## Copilot Analyst AI integration

The app provides two practical options:

1. **Local brief only** — no external call. It summarizes off-target KPIs, trend direction, anomalies and estimates.
2. **Remote connector** — sends a compact, user-approved context to either:
   - an Azure OpenAI chat-completions deployment; or
   - an authenticated Copilot/agent HTTP endpoint.

Microsoft 365 Copilot Analyst is not treated as an anonymously callable public API. In an enterprise deployment, expose the approved Copilot Studio/agent workflow through your organization's authenticated endpoint, then select **Copilot agent HTTP endpoint**. The endpoint receives:

```json
{
  "question": "...",
  "context": "compact JSON string",
  "system_prompt": "..."
}
```

It should return JSON containing one of: `answer`, `response`, `message`, `output` or `text`.

For Azure OpenAI, the UI asks for endpoint, deployment, API version and key. For production, move those inputs into an authenticated backend or adapt `ai_agent.py` to read Streamlit secrets; do not expose durable keys to general users.

The app requires explicit confirmation before any compact dataset context is sent remotely. Sample rows are excluded unless the user separately enables them.

## Practical operating notes

- Uploaded data is held in Streamlit session memory and is not included in preset files.
- A Streamlit rerun or instance restart can clear session data; re-sync the source or reload the preset.
- “Latest” means the last row in the current filtered order. Sort source data by date before upload for deterministic current-value behavior.
- Next-point estimates use a simple linear slope. Anomaly flags use a rolling z-score threshold of 2. These are screening indicators, not causal or engineering models.
- HTML is used instead of server-side PDF generation to avoid OS-level dependencies on Streamlit Cloud. Users can open the HTML report and print/save it as PDF from the browser.
- For very large data, make the pipeline return an aggregated extract rather than pushing millions of raw rows into a browser session.
