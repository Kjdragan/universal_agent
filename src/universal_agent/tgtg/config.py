"""Configuration for the TGTG sniping module, loaded from environment variables."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Auth ──────────────────────────────────────────────────────────────────────
TGTG_EMAIL: str = os.getenv("TGTG_EMAIL", "")
TGTG_ACCESS_TOKEN: str = os.getenv("TGTG_ACCESS_TOKEN", "")
TGTG_REFRESH_TOKEN: str = os.getenv("TGTG_REFRESH_TOKEN", "")
TGTG_USER_ID: str = os.getenv("TGTG_USER_ID", "")
TGTG_COOKIE: str = os.getenv("TGTG_COOKIE", "")

# ── Location ──────────────────────────────────────────────────────────────────
# Your target area coordinates (where the stores you want are located).
# These are sent in the API request body — NOT tied to your proxy's IP.
TGTG_LATITUDE: float = float(os.getenv("TGTG_LATITUDE", "51.5074"))   # default: London
TGTG_LONGITUDE: float = float(os.getenv("TGTG_LONGITUDE", "-0.1278"))
TGTG_RADIUS: int = int(os.getenv("TGTG_RADIUS", "5"))  # km

# ── Watched items ─────────────────────────────────────────────────────────────
# Comma-separated TGTG item IDs to snipe, e.g. "123456,789012"
# Leave empty to snipe all favourites.
_raw_items = os.getenv("TGTG_WATCHED_ITEMS", "")
TGTG_WATCHED_ITEMS: list[str] = [i.strip() for i in _raw_items.split(",") if i.strip()]

# ── Behaviour ─────────────────────────────────────────────────────────────────
# Master switch: automatically call create_order() when stock detected.
TGTG_AUTO_PURCHASE: bool = os.getenv("TGTG_AUTO_PURCHASE", "false").lower() == "true"

# Bags to reserve per item when auto-purchasing.
TGTG_ORDER_COUNT: int = int(os.getenv("TGTG_ORDER_COUNT", "1"))

# ── Polling intervals (seconds) ───────────────────────────────────────────────
# Smart adaptive: fast near pickup windows, slow otherwise.
POLL_IDLE: int = int(os.getenv("TGTG_POLL_IDLE", "300"))          # 5 min outside window
POLL_ACTIVE: int = int(os.getenv("TGTG_POLL_ACTIVE", "30"))       # 30 s within 90 min of window
POLL_SNIPING: int = int(os.getenv("TGTG_POLL_SNIPING", "15"))     # 15 s when stock just appeared
POLL_WINDOW_MINUTES: int = int(os.getenv("TGTG_POLL_WINDOW_MINUTES", "90"))

# ── Proxies ───────────────────────────────────────────────────────────────────
# Rotating residential proxies keep DataDome from fingerprinting a single IP.
#
# Option A — explicit list:
#   TGTG_PROXIES=http://user:pass@host:port,http://user:pass@host2:port
#
# Option B — auto-built from shared Webshare credentials (same vars used by the
#   YouTube transcript module).  If TGTG_PROXIES is not set but
#   WEBSHARE_PROXY_USER / WEBSHARE_PROXY_PASS are present, a single
#   Webshare rotating-residential URL is constructed automatically.
#
# Webshare rotating endpoint:  proxy.webshare.io:80  (HTTP)
# Sticky-session endpoint:     proxy.webshare.io:80  with -country suffix user
_WEBSHARE_HOST = os.getenv("WEBSHARE_PROXY_HOST", "proxy.webshare.io")
_WEBSHARE_PORT = os.getenv("WEBSHARE_PROXY_PORT", "80")

def _build_proxy_list() -> list[str]:
    raw = os.getenv("TGTG_PROXIES", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]

    # Fall back to Webshare shared credentials
    user = (os.getenv("PROXY_USERNAME") or os.getenv("WEBSHARE_PROXY_USER") or "").strip()
    pw   = (os.getenv("PROXY_PASSWORD") or os.getenv("WEBSHARE_PROXY_PASS") or "").strip()
    if user and pw:
        return [f"http://{user}:{pw}@{_WEBSHARE_HOST}:{_WEBSHARE_PORT}"]
    return []

TGTG_PROXIES: list[str] = _build_proxy_list()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_HOST: str = os.getenv("TGTG_DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT: int = int(os.getenv("TGTG_DASHBOARD_PORT", "8765"))

# ── Token persistence ─────────────────────────────────────────────────────────
CREDENTIALS_FILE: Path = Path(os.getenv("TGTG_CREDENTIALS_FILE", ".tgtg_credentials.json"))

# ── Item catalog (SQLite) ─────────────────────────────────────────────────────
TGTG_DB_FILE: Path = Path(os.getenv("TGTG_DB_FILE", ".tgtg_catalog.db"))


def load_saved_credentials() -> dict:
    """Load previously saved auth tokens from disk."""
    if CREDENTIALS_FILE.exists():
        return json.loads(CREDENTIALS_FILE.read_text())
    return {}


def save_credentials(creds: dict) -> None:
    """Persist auth tokens to disk so we don't re-login on every run."""
    CREDENTIALS_FILE.write_text(json.dumps(creds, indent=2))
