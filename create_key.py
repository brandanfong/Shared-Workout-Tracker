"""Create an API key for a person.

Usage:
    python create_key.py brandan "iPhone shortcut"

Reads DATABASE_URL from the environment, or from a .env file in this folder.
The key is printed ONCE. Only its hash is stored, so it can't be recovered.
"""
import os
import sys
from pathlib import Path

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

if len(sys.argv) < 2:
    sys.exit('Usage: python create_key.py <name> ["label"]')
if "DATABASE_URL" not in os.environ:
    sys.exit("DATABASE_URL is not set (put it in .env or your environment).")

from sqlalchemy.orm import Session  # noqa: E402

from main import engine, issue_key  # noqa: E402

name = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else None

print(f"Database host: {engine.url.host}")
with Session(engine) as db:
    key = issue_key(db, name, label)

print(f"\nKey for {name.strip().lower()} (shown once, save it now):\n{key}\n")