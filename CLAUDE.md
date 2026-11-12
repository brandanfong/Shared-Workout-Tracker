# Shared Workout Tracker

FastAPI + Postgres (Neon) app. Front end is one file: `static/index.html` (no build step).
Start the server with `python -m app` ([app/__main__.py](app/__main__.py)). It reads `.env` itself
(`.env` wins over anything left set in the shell) and picks the database from a flag.

All commands are PowerShell, run from the project folder.

## Run the app

```powershell
.\venv\Scripts\python.exe -m app            # REAL data (DATABASE_URL from .env)
.\venv\Scripts\python.exe -m app --demo     # DEMO data (DEMO_DATABASE_URL from .env)
```

Open http://127.0.0.1:8000. Also takes `--port 8001` and `--no-reload`. Nothing is left set in
the PowerShell window, so switching between real and demo is just a different command.

- Real mode: scoring follows `SCORING_ENABLED` in `.env` (off by default).
- Demo mode: scoring is on, and `SCORING_START_DATE` is read from the demo DB (its first scored
  week), so it always matches the last seed run. Demo API keys are fixed: `demo-a`, `demo-b`, ...
- Under the hood it's uvicorn; `app/config.py` reads `DATABASE_URL` from the environment. Running
  `uvicorn app.main:app --env-file .env` directly still works for real data.

**Optional: re-seed the demo** only to refresh the fake data (it is generated relative to today,
so it drifts), change the `PEOPLE` list, or after the demo branch is reset. This WIPES the demo DB;
`python -m app --demo` picks up the new start date automatically:

```powershell
.\venv\Scripts\python.exe scripts\seed_demo.py
```

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
(`app/db.py`, `app/setup_db.py`, `tests/conftest.py`) can break unrelated tests.

## TODO.md

When the user confirms a feature is complete, remove its entry from `TODO.md`. Don't remove items
before that confirmation, even if the code and tests look done.

## Other

- New API key: `.\venv\Scripts\python.exe scripts\create_key.py <name> "<label>"` (uses `DATABASE_URL` from env or `.env`).
- Code lives in the `app/` package (endpoints in `app/routers/`); one-off CLI scripts live in `scripts/`.
- Schema changes go through Alembic: edit `app/models.py`, then
  `.\venv\Scripts\alembic.exe revision --autogenerate -m "..."` and review the file in `alembic/versions/`.
  Never use `create_all` or hand-written `ALTER TABLE` at startup. The app runs `alembic upgrade head` on
  startup (`app/setup_db.py`); tests rebuild the test DB through the migrations each run.
- Settings and "today" live in `app/config.py`. Read them as `config.SCORING_ENABLED` / `config.today()`,
  not `from app.config import ...`, so tests can monkeypatch them in one place.
- More detail (settings table, API, layout): see README.md.
