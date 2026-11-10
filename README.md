# Shared Workout Tracker

A small web app for two people (or a few) to track their daily activity together, earn
shared points for it, and spend those points on rewards.

Each person's phone sends their daily exercise minutes and active calories. The site
shows a shared calendar and trend charts, scores the activity against each person's own
goals, and keeps one shared points balance that you can redeem for rewards.

## What it does

- **Calendar**: who had a workout day, with each person in their own color. A day when
  everyone worked out is solid green with a star. Tap a name to hide or show someone,
  double-tap to show only them.
- **Trends**: minutes and calories over time with a 7-day average and goal lines.
- **Personal goals**: each person sets their own daily minutes and calories, plus a
  weekly workout target. Changes are kept as history, so every past day is judged by the
  goal that applied on that day.
- **Points and rewards**: points go into one shared pot. Rewards have a cost, optional
  cooldown, and can be one-time or repeatable. Everything that changes the balance is a
  row in an append-only ledger, so the history is always auditable. Mistakes are fixed
  with an adjustment entry, never by editing a row.
- **Colors**: everyone picks their own color (swatches or any color, with checks so
  colors stay readable and easy to tell apart).

## How points work

Points are only ever added by activity. The only way the balance goes down is redeeming
a reward. Missing data counts as zero, never as a penalty.

| What | When it is paid | Points |
|---|---|---|
| Daily calorie goal | 2 days after each day (see below) | 100% of goal +1, 150% +2, 200% +3 (highest tier only) |
| Workout day (minutes **and** calories goals both met) | 2 days after each day | +2 each |
| Hit calorie goal on 6 / 7 days in the week | when the week closes | +3 / +5 |
| Hit your weekly workout target | when the week closes | +4 |
| Team bonus (everyone had a successful week) | when the week closes | +4 |
| Streak multiplier on the week's points | when the week closes | 2-3 weeks x1.1, 4-7 x1.25, 8+ x1.5 |
| Calorie consistency over a calendar month | when the month closes | 80% of days +5, 90% +10, 100% +20 |

A day is paid two days after it, because the phone sends today **and yesterday** on every
sync: Monday's numbers can still change during Tuesday, so Monday is paid on Wednesday. If
a late sync raises a day after it was paid, the difference is added when the week closes.

A week is Monday to Sunday and is scored the Tuesday after it ends. A "successful week"
needs 5+ calorie-goal days and your own weekly workout target; the pair succeeds only if
everyone does. A blocked week resets the streak but everyone keeps their direct points.

Scored weeks are frozen: data that arrives later does not change points already paid.
The full numbers live in [scoring.py](scoring.py).

**Scoring is off by default.** Nothing is paid until you set `SCORING_ENABLED=true`. While
it is off the site shows a "Scoring is paused" banner and your activity is still stored.
Weeks before `SCORING_START_DATE` are never scored.

## How the data gets in

Each person's iPhone Shortcut sends today's and yesterday's totals to `POST /log` with
their personal API key in an `X-API-Key` header:

```json
{ "date": "2026-11-09", "minutes": 45, "calories": 520 }
```

The server keeps the larger value for a day, so repeated syncs can only raise a day.

## Setup

You need Python 3.13 and a Postgres database (this project uses [Neon](https://neon.tech)).

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Create a `.env` file in the project folder (it is git-ignored, never commit it):

```
DATABASE_URL=postgresql://user:password@host/dbname
TEST_DATABASE_URL=postgresql://user:password@other-host/other-dbname
```

`TEST_DATABASE_URL` must be a **different, throwaway database** (for example a Neon
branch). The tests and the demo script wipe it.

Run the app:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload --env-file .env
```

Then open http://127.0.0.1:8000. Tables are created automatically on startup.

### Give each person an API key

```powershell
.\venv\Scripts\python.exe create_key.py brandan "iPhone shortcut"
```

The key is printed **once** (only its hash is stored). Paste it into the website's key
box and into the person's Shortcut. Run it again to add another key or another person.

### Settings (environment variables)

All optional.

| Variable | Default | What it does |
|---|---|---|
| `SCORING_ENABLED` | `false` | Turn points on. Leave off while loading old data. |
| `SCORING_START_DATE` | `2026-11-09` | First week that can be scored. Set to a Monday. |
| `MIN_WORKOUT_MINUTES` | `30` | Default minutes goal for someone who has not set one |
| `MIN_WORKOUT_CALORIES` | `400` | Default calorie goal |
| `WEEKLY_WORKOUT_GOAL` | `2` | Default workouts per week |

## Tests

```powershell
Get-Content .env | % { if ($_ -match '^\s*TEST_DATABASE_URL\s*=\s*(.*)$') { $env:TEST_DATABASE_URL = $Matches[1].Trim() } }
.\venv\Scripts\python.exe -m pytest -q
```

The tests **truncate every table** in `TEST_DATABASE_URL` and refuse to run if it is the
same database as `DATABASE_URL`. GitHub Actions runs them on every push
([.github/workflows/tests.yml](.github/workflows/tests.yml)).

## Demo data

[seed_demo.py](seed_demo.py) fills the **test** database with three fake people and about
eight weeks of activity, and scores it, so you can see the calendar, charts, and rewards
without touching real data. Use it for UI work or to try things like many users.

```powershell
.\venv\Scripts\python.exe seed_demo.py
```

It wipes the test database first and refuses to run against `DATABASE_URL`. It prints demo
API keys and the commands to start the server pointed at the demo data. Add or change
people in the `PEOPLE` list at the top of the script. Running the tests wipes the demo
data again, so re-run the script afterwards.

## Project layout

| Path | What it is |
|---|---|
| [main.py](main.py) | The FastAPI app: database tables, API, scoring runner |
| [scoring.py](scoring.py) | The scoring rules (pure functions, no database) |
| [colors.py](colors.py) | Preset colors and the rules a chosen color must follow |
| [static/index.html](static/index.html) | The whole front end (one page, no build step) |
| [create_key.py](create_key.py) | Make an API key for a person |
| [seed_demo.py](seed_demo.py) | Fill the test database with demo data |
| [tests/](tests/) | The test suite |

## API overview

All endpoints need an `X-API-Key` header except `/`, `/health`, and the page itself.

| Endpoint | Purpose |
|---|---|
| `POST /log` | Phone sends a day's minutes and calories |
| `GET /api/calendar?month=YYYY-MM` | Month view data, goals and colors |
| `GET /api/activity?days=30` | Chart data |
| `GET /api/me`, `PATCH /api/me/settings`, `PATCH /api/me/color` | Your goals and color |
| `GET /api/balance`, `GET /api/ledger` | Shared balance and the points history |
| `GET/POST /api/rewards`, `PATCH /api/rewards/{id}`, `POST /api/rewards/{id}/redeem` | The rewards menu |
| `POST /api/adjust` | Correct a mistake with a new ledger entry |
| `POST /api/scoring/run` | Check for weeks or months ready to score (also runs on every sync) |
