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
.\venv\Scripts\python.exe -m pytest -q
```

No setup needed: `tests/conftest.py` reads `TEST_DATABASE_URL` straight from `.env` (the `.env` value wins over anything set in the shell).

`TEST_DATABASE_URL` in `.env` points at a **local Postgres 17** (Windows service
`postgresql-x64-17`, database `workout_test`, user `postgres`). The full suite (206 tests) runs in
about 17 seconds. Tests used to run against a remote Neon branch and took ~6 minutes because every
query was a network round trip, so don't point them at a remote DB.

**New machine setup** (Postgres is a system program, not a pip package, so it isn't in
`requirements.txt`):

```powershell
winget install --id PostgreSQL.PostgreSQL.17      # set a password for the postgres user when asked
& 'C:\Program Files\PostgreSQL\17\bin\psql.exe' -h localhost -U postgres -c "CREATE DATABASE workout_test"
```

Then add `TEST_DATABASE_URL=postgresql://postgres:<password>@localhost:5432/workout_test` to `.env`.

If pytest fails to connect, check that the service is running (`Get-Service postgresql-x64-17`;
start it with `Start-Service postgresql-x64-17` from an admin window). Never point the tests at the
real or demo URL as a workaround.

Tests TRUNCATE every table in `TEST_DATABASE_URL`. It must be a throwaway DB, different from both
the real and demo databases.

Targeted runs are still handy while building a feature:

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_scoring.py            # one file
.\venv\Scripts\python.exe -m pytest -q tests/test_scoring.py::test_name # one test
.\venv\Scripts\python.exe -m pytest -q -k scoring                       # match by name
.\venv\Scripts\python.exe -m pytest -q --lf                             # rerun last failures
```

Run the full suite before the user confirms a feature as done or before pushing, since shared code
(`main.py`, DB setup) can break unrelated tests.

## TODO.md

When the user confirms a feature is complete, remove its entry from `TODO.md`. Don't remove items
before that confirmation, even if the code and tests look done.

## Other

- New API key: `.\venv\Scripts\python.exe create_key.py <name> "<label>"` (uses `DATABASE_URL` from env or `.env`).
- More detail (settings table, API, layout): see README.md.
