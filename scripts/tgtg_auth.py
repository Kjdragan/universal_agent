"""
One-shot TGTG authentication script.

Starts the TGTG polling flow in a background thread, then waits for a magic
link to be written to a shared file (by an external process that reads Gmail),
follows the link, and saves credentials to disk.

Usage:
  .venv/bin/python scripts/tgtg_auth.py

The script writes the current status to a secure temp file so the
caller can track progress.  The path is printed on startup.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tgtg import TgtgClient
from src.universal_agent.tgtg.config import CREDENTIALS_FILE

# ── Configuration ─────────────────────────────────────────────────────────────

# Read email from environment; never hardcode personal addresses here.
EMAIL: str = os.getenv("TGTG_EMAIL", "your@email.com")

# Secure temp files with unique names and restricted permissions (0o600 on Unix).
_state_fd, _state_path = tempfile.mkstemp(suffix=".json", prefix="tgtg_auth_state_")
os.close(_state_fd)
STATE_FILE = Path(_state_path)

_link_fd, _link_path = tempfile.mkstemp(suffix=".txt", prefix="tgtg_magic_link_")
os.close(_link_fd)
LINK_FILE = Path(_link_path)

# ── Shared state ──────────────────────────────────────────────────────────────

_result: dict = {}
_error: list[str] = []
_done = threading.Event()


# ── Helper functions (module-level for testability) ────────────────────────────

def _write_state(state: str, extra: dict | None = None) -> None:
    data = {"state": state, "email": EMAIL, **(extra or {})}
    STATE_FILE.write_text(json.dumps(data, indent=2))
    print(f"[AUTH] {state}", flush=True)


def _login_thread() -> None:
    try:
        _write_state("STARTING", {"msg": "Sending auth request to TGTG…"})
        client = TgtgClient(email=EMAIL)
        # get_credentials() triggers login() which sends the email and polls
        creds = client.get_credentials()
        _result["creds"] = creds
        _result["client"] = client
        _write_state("SUCCESS", {"msg": "Tokens received!"})
    except Exception as exc:
        _error.append(str(exc))
        _write_state("ERROR", {"msg": str(exc)})
    finally:
        _done.set()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting TGTG auth flow for {EMAIL}", flush=True)
    print("This will send a magic link email to that address.", flush=True)
    print(f"State file: {STATE_FILE}", flush=True)

    t = threading.Thread(target=_login_thread, daemon=True)
    t.start()

    # Give TGTG ~5s to send the email before we expect Gmail to have it
    time.sleep(5)
    _write_state("WAITING_FOR_CLICK", {
        "msg": (
            "Email sent. Waiting for magic link to be clicked. "
            f"Write the link URL to {LINK_FILE} or click it manually."
        )
    })

    # Wait up to 110 seconds for the polling to complete
    _done.wait(timeout=110)

    if _error:
        print(f"\n❌ Auth failed: {_error[0]}", flush=True)
        sys.exit(1)

    if not _result.get("creds"):
        print("\n❌ Timed out waiting for magic link click.", flush=True)
        sys.exit(1)

    # Save credentials
    creds = _result["creds"]
    CREDENTIALS_FILE.write_text(json.dumps(creds, indent=2))
    print(f"\n✅ Credentials saved to {CREDENTIALS_FILE}", flush=True)
    print(f"   access_token:  {creds.get('access_token', '')[:24]}…", flush=True)
    print(f"   refresh_token: {creds.get('refresh_token', '')[:24]}…", flush=True)
