"""HTTP utilities (stdlib only)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_TIMEOUT_S = 30
DEBUG = os.environ.get("OPENWEATHER_DEBUG", "").lower() in ("1", "true", "yes")

MAX_RETRIES = 3
RETRY_DELAY_S = 1.0
USER_AGENT = "openweather-skill/1.0 (universal_agent)"


class HTTPError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _log(msg: str) -> None:
    if not DEBUG:
        return
    sys.stderr.write(f"[DEBUG] {msg}\n")
    sys.stderr.flush()


def get_json(
    url: str,
    params: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    retries: int = MAX_RETRIES,
) -> Dict[str, Any]:
    headers = dict(headers or {})
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept", "application/json")

    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full = f"{url}?{qs}" if qs else url
    req = urllib.request.Request(full, headers=headers, method="GET")

    last_error: Optional[HTTPError] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8")
                _log(f"GET {url} -> {resp.status} ({len(body)} bytes)")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = None
            try:
                body = e.read().decode("utf-8")
            except Exception:
                pass
            _log(f"HTTPError {e.code}: {e.reason} body={body[:300] if body else ''}")
            last_error = HTTPError(f"HTTP {e.code}: {e.reason}", e.code, body)

            # Do not retry most 4xx.
            if 400 <= e.code < 500 and e.code != 429:
                raise last_error
        except urllib.error.URLError as e:
            _log(f"URLError: {e.reason}")
            last_error = HTTPError(f"URL Error: {e.reason}")
        except json.JSONDecodeError as e:
            raise HTTPError(f"Invalid JSON response: {e}")
        except (OSError, TimeoutError, ConnectionResetError) as e:
            _log(f"Connection error: {type(e).__name__}: {e}")
            last_error = HTTPError(f"Connection error: {type(e).__name__}: {e}")

        if attempt < retries - 1:
            time.sleep(RETRY_DELAY_S * (attempt + 1))

    if last_error is not None:
        raise last_error
    raise HTTPError("Request failed with no error details")

