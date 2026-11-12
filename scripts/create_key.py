"""Create an API key for a person.

Usage:
    python scripts/create_key.py brandan "iPhone shortcut"

Reads DATABASE_URL from the environment, or from the .env file in the project folder.
The key is printed ONCE. Only its hash is stored, so it can't be recovered.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `import app` works when run as a script

from app.envfile import read_env  # noqa: E402

for k, v in read_env().items():
    os.environ.setdefault(k, v)

if len(sys.argv) < 2:
    sys.exit('Usage: python scripts/create_key.py <name> ["label"]')
if "DATABASE_URL" not in os.environ:
    sys.exit("DATABASE_URL is not set (put it in .env or your environment).")

from sqlalchemy.orm import Session  # noqa: E402

from app.auth import issue_key  # noqa: E402
from app.db import engine  # noqa: E402
from app.migrations import setup_database  # noqa: E402

name = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else None

print(f"Database host: {engine.url.host}")
setup_database()  # create tables if this is a fresh database
with Session(engine) as db:
    key = issue_key(db, name, label)

print(f"\nKey for {name.strip().lower()} (shown once, save it now):\n{key}\n")