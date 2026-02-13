"""Small file cache to respect OpenWeather's ~10-minute per-location recommendation."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    if root:
        return Path(root) / "universal_agent" / "openweather"
    return Path.home() / ".cache" / "universal_agent" / "openweather"


def _key_to_path(key: str) -> Path:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _cache_dir() / f"{h}.json"


def get(key: str, ttl_s: int) -> Optional[Dict[str, Any]]:
    path = _key_to_path(key)
    try:
        raw = path.read_text(encoding="utf-8")
        obj = json.loads(raw)
    except Exception:
        return None

    ts = obj.get("_ts")
    if not isinstance(ts, (int, float)):
        return None
    if (time.time() - float(ts)) > ttl_s:
        return None

    data = obj.get("data")
    return data if isinstance(data, dict) else None


def set(key: str, data: Dict[str, Any]) -> None:
    path = _key_to_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {"_ts": time.time(), "data": data}
    path.write_text(json.dumps(obj, ensure_ascii=True), encoding="utf-8")

