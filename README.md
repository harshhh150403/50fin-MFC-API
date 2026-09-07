# 50fin MFC API

An internal web tool for looking up an investor's mutual fund lien holdings by PAN and mobile number. It shows the raw holdings returned by the MFC API and an audited aggregation of those holdings.

The tool has no login, user accounts, or database. It runs as two local processes:

- FastAPI validates input and makes the upstream MFC API request.
- Streamlit provides the form, investor details, filters, tables, and CSV downloads.

## Requirements

- Python 3.11 or newer
- Valid MFC API client credentials
- Network access to the configured MFC API base URL

## Install

Clone or pull the repository, then open a terminal in its root folder.

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

If `.env` already exists, do not overwrite it.

## Configure `.env`

Open `.env` and replace only the dummy MFC values with the real values supplied by the API provider:

```dotenv
MFC_BASE_URL=https://api.example.com
MFC_CLIENT_ID=replace-with-client-id
MFC_CLIENT_SECRET=replace-with-client-secret
MFC_REQUEST_TIMEOUT_SECONDS=30

BACKEND_API_URL=http://127.0.0.1:8000
BACKEND_REQUEST_TIMEOUT_SECONDS=30
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000

STREAMLIT_SERVER_ADDRESS=127.0.0.1
STREAMLIT_SERVER_PORT=8501
```

`MFC_BASE_URL` is the upstream provider URL. `BACKEND_API_URL` is the URL Streamlit uses to reach FastAPI. The host and port values control where the two local processes listen.

Never commit `.env`. The Postman collection and `.env` are both ignored by Git.

## Start the tool

Open two terminals in the project root. Load `.env` into each terminal before starting its process so every host and port comes from configuration.

### macOS or Linux

Terminal 1 — FastAPI backend:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python -m uvicorn backend:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
```

Terminal 2 — Streamlit frontend:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python -m streamlit run app.py --server.address "$STREAMLIT_SERVER_ADDRESS" --server.port "$STREAMLIT_SERVER_PORT"
```

### Windows PowerShell

Run this block once in each terminal to activate the virtual environment and load `.env`:

```powershell
.venv\Scripts\Activate.ps1
Get-Content .env | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
    $name, $value = $_ -split "=", 2
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}
```

Then start FastAPI in terminal 1:

```powershell
python -m uvicorn backend:app --host $env:BACKEND_HOST --port $env:BACKEND_PORT
```

Start Streamlit in terminal 2:

```powershell
python -m streamlit run app.py --server.address $env:STREAMLIT_SERVER_ADDRESS --server.port $env:STREAMLIT_SERVER_PORT
```

Open the Streamlit URL shown in terminal 2. With the example local settings it is `http://127.0.0.1:8501`.

## Verify the backend

After FastAPI starts, open `${BACKEND_API_URL}/health` or run the command for your shell.

macOS or Linux:

```bash
curl "$BACKEND_API_URL/health"
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Uri "$($env:BACKEND_API_URL)/health"
```

A configured backend returns:

```json
{"status":"ok","credentials_loaded":true}
```

If required credentials are absent or blank, FastAPI stops during startup with a message naming the missing `.env` variables.

## Use the tool

1. Enter the investor's PAN and mobile number.
2. Click **Look up holdings**. No request is made while typing or changing filters.
3. Review the raw holdings and the aggregated holdings.
4. Use each table's independent filters or download its current filtered rows as CSV.

The aggregation groups rows on `isin`, `folio`, `lienSubRefNo`, and `lienRefNo`. It sums `lienHoldUnits`, retains the first descriptive and `TotalLienUnits` values, and shows the number of source rows. The page warns about missing grouping keys or inconsistent `TotalLienUnits` values and confirms that aggregation preserved the complete units total.

The upstream POST may have server-side effects. The tool never retries automatically, refreshes in the background, or submits without an explicit click. If a timeout occurs, confirm whether the request was processed before submitting it again.

## Rotate credentials

1. Obtain the replacement client ID and client secret through the approved channel.
2. Stop the FastAPI process.
3. Update `MFC_CLIENT_ID` and `MFC_CLIENT_SECRET` in the local `.env` file.
4. Restart FastAPI.
5. Confirm `${BACKEND_API_URL}/health` reports `"credentials_loaded": true`.

No source-code change is required. Do not place credentials in Python files, Git history, screenshots, logs, or issue descriptions.

## Troubleshooting

- **Backend unavailable:** Start FastAPI using the command above. If it is already running, confirm `BACKEND_API_URL` matches its address.
- **Timeout:** The outcome is unknown. Check the backend and provider status before deciding whether to submit again.
- **Credentials rejected:** Verify `MFC_CLIENT_ID` and `MFC_CLIENT_SECRET`, then restart FastAPI.
- **PAN or mobile rejected:** Correct the format shown beneath the form fields and submit again.
- **Unexpected non-JSON response:** Check the FastAPI terminal and the upstream provider status before submitting again.
- **No lien holdings:** This is a valid result. The page says so and does not show empty tables.

## Scope

Deployment is intentionally out of scope. All service locations, request timeouts, and listening ports are configured through environment variables so deployment can be added later without hardcoded local addresses.
