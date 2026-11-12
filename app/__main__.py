"""Start the server on http://127.0.0.1:8000.

    python -m app            real data (DATABASE_URL from .env)
    python -m app --demo     demo data (DEMO_DATABASE_URL from .env, scoring on)

Settings come from .env, so nothing needs setting in the shell first, and
nothing is left set in the shell afterwards.
"""
import argparse
import os
import sys

import uvicorn

from app.envfile import ENV_PATH, read_env, same_db


def demo_scoring_start(url: str) -> str | None:
    """The demo data's scoring start is the first week seed_demo.py scored, so
    read it from the demo database instead of having to remember it."""
    from sqlalchemy import create_engine, text

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            first = conn.execute(
                text("SELECT min(period) FROM scoring_periods WHERE kind = 'week'")
            ).scalar()
    except Exception:
        return None
    finally:
        engine.dispose()
    return first[len("wk-"):] if first else None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="Start the workout tracker.")
    parser.add_argument("--demo", action="store_true", help="use the demo database instead of real data")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="don't restart when code changes")
    args = parser.parse_args()

    env = read_env()
    if not env:
        sys.exit(f"No .env found at {ENV_PATH}.")
    # .env wins over the shell, so a value left over from an old window can't
    # point the server at the wrong database.
    os.environ.update(env)

    if args.demo:
        demo_url = env.get("DEMO_DATABASE_URL")
        if not demo_url:
            sys.exit("DEMO_DATABASE_URL is not set in .env.")
        if same_db(demo_url, env.get("DATABASE_URL")):
            sys.exit("DEMO_DATABASE_URL is the same database as DATABASE_URL. Refusing to start demo mode.")
        start = demo_scoring_start(demo_url)
        if not start:
            sys.exit("The demo database has no scored weeks. Run: python scripts/seed_demo.py")
        os.environ["DATABASE_URL"] = demo_url
        os.environ["SCORING_ENABLED"] = "true"
        os.environ["SCORING_START_DATE"] = start
        print(f"DEMO data (scoring from {start}). Keys: demo-a, demo-b, ...")
    else:
        if not env.get("DATABASE_URL"):
            sys.exit("DATABASE_URL is not set in .env.")
        # Drop demo-mode settings left in this shell unless .env sets them.
        for key in ("SCORING_ENABLED", "SCORING_START_DATE"):
            if key not in env:
                os.environ.pop(key, None)
        print("REAL data.")

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, reload=not args.no_reload)


if __name__ == "__main__":
    main()
