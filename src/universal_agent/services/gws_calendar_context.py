"""Optional GWS calendar context for proactive digests."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
import shutil
import subprocess
from typing import Any
from zoneinfo import ZoneInfo


def today_calendar_context(
    *,
    timezone_name: str = "America/Chicago",
    calendar_id: str = "primary",
    max_results: int = 8,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    """Return today's calendar events via gws, or a non-fatal unavailable result."""
    if not shutil.which("gws"):
        return {"ok": False, "reason": "gws_binary_not_found", "events": []}
    tz = ZoneInfo(timezone_name)
    today = datetime.now(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    params = {
        "calendarId": calendar_id,
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max(1, min(int(max_results or 8), 25)),
    }
    cmd = ["gws", "calendar", "events", "list", "--params", json.dumps(params)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "gws_timeout", "events": []}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "events": []}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        return {"ok": False, "reason": detail or f"gws_exit_{result.returncode}", "events": []}
    try:
        data = json.loads(result.stdout or "{}")
    except Exception as exc:
        return {"ok": False, "reason": f"invalid_json: {exc}", "events": []}
    events = [_normalize_event(item) for item in data.get("items", []) if isinstance(item, dict)]
    return {"ok": True, "reason": "", "events": events}


def _normalize_event(item: dict[str, Any]) -> dict[str, Any]:
    start = item.get("start") if isinstance(item.get("start"), dict) else {}
    return {
        "id": str(item.get("id") or ""),
        "summary": str(item.get("summary") or "(untitled)"),
        "start": str(start.get("dateTime") or start.get("date") or ""),
        "htmlLink": str(item.get("htmlLink") or ""),
    }
