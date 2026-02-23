"""Generic anti-block egress adapter with endpoint failover."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable


ANTI_BOT_MARKERS = (
    "request_blocked",
    "ipblocked",
    "too many requests",
    "captcha",
    "cloud provider",
    "youtube is blocking requests from your ip",
    "blocked by youtube",
    "temporarily blocked",
    "forbidden",
)


def parse_endpoint_list(raw: str, *, fallback: str = "") -> list[str]:
    endpoints: list[str] = []
    for part in (raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        if not item.startswith("http://") and not item.startswith("https://"):
            continue
        endpoints.append(item)
    if not endpoints and fallback.strip():
        endpoints.append(fallback.strip())
    deduped: list[str] = []
    for endpoint in endpoints:
        if endpoint not in deduped:
            deduped.append(endpoint)
    return deduped


def detect_anti_bot_block(payload: dict[str, Any]) -> bool:
    failure_class = str(payload.get("failure_class") or "").strip().lower()
    error = str(payload.get("error") or "").strip().lower()
    detail = str(payload.get("detail") or "").strip().lower()
    source = f"{failure_class}\n{error}\n{detail}"
    if any(marker in source for marker in ANTI_BOT_MARKERS):
        return True
    http_status = int(payload.get("http_status") or 0)
    if http_status in {401, 403, 429}:
        return True
    return False


def _post_json(
    *,
    endpoint: str,
    payload: dict[str, Any],
    token: str,
    timeout_seconds: int,
    headers: dict[str, str] | None,
) -> dict[str, Any]:
    req_headers = {"content-type": "application/json"}
    if headers:
        req_headers.update(headers)
    if token:
        req_headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(5, int(timeout_seconds))) as resp:
            body = resp.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"ok": False, "error": "invalid_json_response", "detail": body[:400]}
            if not isinstance(parsed, dict):
                parsed = {"ok": False, "error": "invalid_response_shape"}
            parsed.setdefault("http_status", int(resp.status))
            parsed["_endpoint"] = endpoint
            return parsed
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return {
            "ok": False,
            "status": "failed",
            "error": "http_error",
            "http_status": int(exc.code),
            "detail": body[:400],
            "_endpoint": endpoint,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "failed",
            "error": "request_exception",
            "detail": f"{type(exc).__name__}: {exc}",
            "_endpoint": endpoint,
        }


def post_json_with_failover(
    *,
    endpoints: list[str],
    payload: dict[str, Any],
    token: str = "",
    timeout_seconds: int = 30,
    headers: dict[str, str] | None = None,
    success_predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    if not endpoints:
        return {"ok": False, "status": "failed", "error": "no_endpoints_configured", "endpoint_attempts": []}
    attempts: list[dict[str, Any]] = []
    last: dict[str, Any] = {"ok": False, "status": "failed", "error": "no_attempts"}
    predicate = success_predicate or (lambda result: bool(result.get("ok")))

    for endpoint in endpoints:
        result = _post_json(
            endpoint=endpoint,
            payload=payload,
            token=token,
            timeout_seconds=timeout_seconds,
            headers=headers,
        )
        attempts.append(
            {
                "endpoint": endpoint,
                "ok": bool(result.get("ok")),
                "status": str(result.get("status") or ""),
                "error": str(result.get("error") or ""),
                "failure_class": str(result.get("failure_class") or ""),
                "http_status": int(result.get("http_status") or 0),
                "anti_bot_suspected": detect_anti_bot_block(result),
            }
        )
        last = result
        if predicate(result):
            out = dict(result)
            out["endpoint_attempts"] = attempts
            return out

    out = dict(last)
    out["endpoint_attempts"] = attempts
    return out
