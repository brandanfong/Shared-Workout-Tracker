# Shared Workout Tracker

FastAPI + Postgres (Neon) app. Front end is one file: `static/index.html` (no build step).
`main.py` reads `os.environ["DATABASE_URL"]` directly and does NOT load `.env` itself, so the
environment must be set before starting the server (see below). Starting without it gives
`KeyError: 'DATABASE_URL'`.

All commands are PowerShell, run from the project folder.

## Quick start (copy/paste)

**Live data** (use a fresh PowerShell window):

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload --env-file .env
```

**Demo data** (paste all three lines at once; no `--env-file`):

```powershell
Get-Content .env | % { if ($_ -match '^\s*DEMO_DATABASE_URL\s*=\s*(.*)$') { $env:DATABASE_URL = $Matches[1].Trim() } }; $env:SCORING_ENABLED = 'true'; $env:SCORING_START_DATE = '2026-09-14'
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Details and gotchas are below.

## Run against the REAL database

`.env` holds `DATABASE_URL`; pass it with `--env-file`:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload --env-file .env
```

Open http://127.0.0.1:8000. Scoring is off by default (`SCORING_ENABLED` unset/false).

## Run in DEMO mode (fake data, separate Neon branch)

`.env` must contain `DEMO_DATABASE_URL`. Env vars only affect the current PowerShell window.

The demo data already lives in the Neon demo branch, so the normal way to start is just this
(no seeding needed):

```powershell
# Point this window at the demo DB and start the server
Get-Content .env | % { if ($_ -match '^\s*DEMO_DATABASE_URL\s*=\s*(.*)$') { $env:DATABASE_URL = $Matches[1].Trim() } }
$env:SCORING_ENABLED = 'true'; $env:SCORING_START_DATE = '2026-09-14'
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

`SCORING_START_DATE` must be the date from the last seed run (`2026-09-14` was the one in the
README; use whatever `seed_demo.py` printed if it differs).

**Optional: re-seed** only to refresh the fake data (it is generated relative to today, so it
drifts), change the `PEOPLE` list, or after the demo branch is reset. This WIPES the demo DB and
prints a new `SCORING_START_DATE` to use:

```powershell
.\venv\Scripts\python.exe seed_demo.py
```

Do NOT add `--env-file .env` in demo mode: it would load the real `DATABASE_URL` over the demo one.
Demo API keys are fixed: `demo-a`, `demo-b`, ... (only work on the demo DB).

Gotcha: `$env:DATABASE_URL` stays set in that window. To go back to real data, open a new
PowerShell window (or `Remove-Item Env:DATABASE_URL, Env:SCORING_ENABLED, Env:SCORING_START_DATE`).

## Tests

```powershell
Get-Content .env | % { if ($_ -match '^\s*TEST_DATABASE_URL\s*=\s*(.*)$') { $env:TEST_DATABASE_URL = $Matches[1].Trim() } }
.\venv\Scripts\python.exe -m pytest -q
```

Tests TRUNCATE every table in `TEST_DATABASE_URL`. It must be a throwaway DB, different from both
the real and demo databases.

## Other

- New API key: `.\venv\Scripts\python.exe create_key.py <name> "<label>"` (uses `DATABASE_URL` from env or `.env`).
- More detail (settings table, API, layout): see README.md.
