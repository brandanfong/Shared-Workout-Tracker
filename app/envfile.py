"""Read the project's .env file. Imports nothing from the app, so scripts can
use it before DATABASE_URL is set."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    """KEY=value pairs from .env (empty if there is no file). utf-8-sig so a
    byte-order mark that Windows editors add doesn't end up in the first key."""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"\s*([A-Za-z_]\w*)\s*=\s*(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip("'\"")
    return env


def same_db(a: str | None, b: str | None) -> bool:
    def norm(url):
        return (url or "").strip().strip("'\"").replace("+psycopg", "", 1)

    return bool(a) and norm(a) == norm(b)
