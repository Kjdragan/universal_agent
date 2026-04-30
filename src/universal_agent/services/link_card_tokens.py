"""Short-lived, one-shot tokens for Link card-details URLs.

When a spend request is approved, the bridge issues a token bound to that
spend_request_id with a TTL (default 15 minutes). The token is delivered via
email or Mission Control link and is the *only* credential needed to view
card details — no auth header, no session cookie, just the token in the URL.

Properties:
  - One-shot: consume() invalidates the token after first successful read.
  - TTL-bounded: tokens past expires_at fail closed regardless of consume state.
  - File-backed: persists across restarts so an in-flight email link still
    works after a deploy.
  - Card-data-free: tokens NEVER store PAN/CVC. The token only authorizes
    "you may call link-cli retrieve --include=card once for this spend_request".
    Card data is fetched fresh from Link CLI on token consume and held only
    in memory long enough to render the response.

Storage path: AGENT_RUN_WORKSPACES/link_card_tokens.json
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from universal_agent import feature_flags


_DEFAULT_TTL_SECONDS = 900  # 15 minutes


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_tokens_path() -> Path:
    override = os.getenv("UA_LINK_CARD_TOKENS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "link_card_tokens.json"


def _load() -> dict[str, Any]:
    path = resolve_tokens_path()
    if not path.exists():
        return {"tokens": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"tokens": {}}


def _save(payload: dict[str, Any]) -> None:
    path = resolve_tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2))
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _ttl_seconds() -> int:
    try:
        return int(feature_flags.link_signed_url_ttl_seconds(default=_DEFAULT_TTL_SECONDS))
    except Exception:
        return _DEFAULT_TTL_SECONDS


def issue(spend_request_id: str, *, ttl_seconds: Optional[int] = None) -> dict[str, Any]:
    """Issue a one-shot token for the given spend request id."""
    if not spend_request_id:
        raise ValueError("spend_request_id required")
    ttl = int(ttl_seconds if ttl_seconds is not None else _ttl_seconds())
    now = time.time()
    token = "tok_" + secrets.token_urlsafe(24)

    payload = _load()
    tokens = payload.setdefault("tokens", {})
    tokens[token] = {
        "spend_request_id": spend_request_id,
        "issued_at": now,
        "expires_at": now + ttl,
        "consumed": False,
        "consumed_at": None,
    }
    _save(payload)
    return {
        "token": token,
        "spend_request_id": spend_request_id,
        "expires_at": now + ttl,
        "ttl_seconds": ttl,
    }


def peek(token: str) -> Optional[dict[str, Any]]:
    """Inspect a token's state without consuming it. Returns None if missing."""
    payload = _load()
    record = (payload.get("tokens") or {}).get(token)
    if record is None:
        return None
    return dict(record)


def consume(token: str) -> dict[str, Any]:
    """Atomically validate + mark a token as consumed.

    Returns:
      {"ok": True, "spend_request_id": "...", "expires_at": ..., "issued_at": ...}
      {"ok": False, "code": "not_found" | "expired" | "already_consumed", "message": "..."}
    """
    if not token or not isinstance(token, str):
        return {"ok": False, "code": "not_found", "message": "Token missing or invalid."}

    payload = _load()
    tokens = payload.get("tokens") or {}
    record = tokens.get(token)
    if record is None:
        return {"ok": False, "code": "not_found", "message": "Token not found."}

    now = time.time()
    if record.get("expires_at", 0) <= now:
        return {
            "ok": False,
            "code": "expired",
            "message": "Token expired.",
            "spend_request_id": record.get("spend_request_id"),
        }

    if record.get("consumed"):
        return {
            "ok": False,
            "code": "already_consumed",
            "message": "Token already used. Card details are one-shot — request a new spend request to view again.",
            "spend_request_id": record.get("spend_request_id"),
        }

    record["consumed"] = True
    record["consumed_at"] = now
    tokens[token] = record
    payload["tokens"] = tokens
    _save(payload)

    return {
        "ok": True,
        "spend_request_id": record["spend_request_id"],
        "expires_at": record["expires_at"],
        "issued_at": record["issued_at"],
    }


def purge_expired(*, older_than_seconds: int = 86400) -> int:
    """Delete tokens whose expires_at is more than `older_than_seconds` ago.

    Returns count of records removed. Safe to call periodically.
    """
    payload = _load()
    tokens = payload.get("tokens") or {}
    cutoff = time.time() - older_than_seconds
    keep = {k: v for k, v in tokens.items() if v.get("expires_at", 0) > cutoff}
    removed = len(tokens) - len(keep)
    if removed:
        payload["tokens"] = keep
        _save(payload)
    return removed
