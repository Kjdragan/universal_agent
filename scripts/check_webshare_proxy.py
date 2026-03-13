#!/usr/bin/env python3
"""
Direct Webshare residential proxy transport probe.

This isolates proxy transport from the rest of the YouTube ingest pipeline so
operators can tell whether failures come from:
1. Missing or stale proxy configuration
2. TCP reachability problems to the proxy endpoint
3. HTTPS CONNECT tunnel failures
4. YouTube-specific failures over an otherwise healthy proxy

The script intentionally reuses the same env precedence as the YouTube ingest
path:
  PROXY_USERNAME / PROXY_PASSWORD
  WEBSHARE_PROXY_USER / WEBSHARE_PROXY_PASS
  WEBSHARE_PROXY_HOST / PROXY_HOST
  WEBSHARE_PROXY_PORT / PROXY_PORT
  PROXY_FILTER_IP_LOCATIONS / PROXY_LOCATIONS / YT_PROXY_FILTER_IP_LOCATIONS / WEBSHARE_PROXY_LOCATIONS
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent.youtube_ingest import _classify_api_error, _parse_proxy_locations

DEFAULT_HTTP_URL = "http://api.ipify.org?format=json"
DEFAULT_HTTPS_URL = "https://api.ipify.org?format=json"
DEFAULT_YOUTUBE_URL = "https://www.youtube.com/generate_204"
STALE_WEBSHARE_HOST = "proxy.webshare.io"
CANONICAL_WEBSHARE_HOST = "p.webshare.io"


def _load_local_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
        return
    except Exception:
        pass

    env_path = os.path.join(os.getcwd(), ".env")
    try:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    key = key.strip()
                    if not key:
                        continue
                    os.environ.setdefault(key, value.strip())
    except Exception:
        pass


def _resolve_proxy_settings() -> dict[str, Any]:
    username = (os.getenv("PROXY_USERNAME") or os.getenv("WEBSHARE_PROXY_USER") or "").strip()
    password = (os.getenv("PROXY_PASSWORD") or os.getenv("WEBSHARE_PROXY_PASS") or "").strip()
    host = (os.getenv("WEBSHARE_PROXY_HOST") or os.getenv("PROXY_HOST") or CANONICAL_WEBSHARE_HOST).strip()
    port_raw = (os.getenv("WEBSHARE_PROXY_PORT") or os.getenv("PROXY_PORT") or "80").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 80
    if port <= 0 or port > 65535:
        port = 80
    locations = _parse_proxy_locations(
        os.getenv("PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("PROXY_LOCATIONS")
        or os.getenv("YT_PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("WEBSHARE_PROXY_LOCATIONS")
        or ""
    )
    warnings: list[str] = []
    if host.strip().lower() == STALE_WEBSHARE_HOST:
        warnings.append(
            "Resolved proxy host is proxy.webshare.io, which is stale for the residential ingest path. "
            "Expected default is p.webshare.io:80."
        )
    return {
        "username": username,
        "password": password,
        "host": host or CANONICAL_WEBSHARE_HOST,
        "port": port,
        "locations": locations,
        "warnings": warnings,
    }


def _proxy_url(settings: dict[str, Any]) -> str:
    user = quote(str(settings["username"]), safe="")
    password = quote(str(settings["password"]), safe="")
    return f"http://{user}:{password}@{settings['host']}:{settings['port']}"


def _redacted_proxy_url(settings: dict[str, Any]) -> str:
    username = str(settings.get("username") or "")
    user_hint = username[:3] + "..." if username else ""
    auth = f"{user_hint}:***@" if username else ""
    return f"http://{auth}{settings['host']}:{settings['port']}"


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=max(1.0, float(timeout_seconds or 10.0))):
            latency_ms = round((time.monotonic() - started) * 1000.0, 1)
            return {
                "ok": True,
                "host": host,
                "port": port,
                "latency_ms": latency_ms,
                "failure_class": "",
                "error": "",
            }
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "host": host,
            "port": port,
            "latency_ms": None,
            "failure_class": _classify_api_error("proxy_tcp_probe_failed", detail),
            "error": detail,
        }


def _read_snippet(response: Any) -> str:
    try:
        body = response.read(240).decode("utf-8", errors="replace")
    except Exception:
        return ""
    return body.strip()


def _request_via_proxy(
    *,
    label: str,
    url: str,
    proxy_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(proxy_handler)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ua-webshare-proxy-check/1.0",
            "Accept": "*/*",
        },
        method="GET",
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=max(1.0, float(timeout_seconds or 15.0))) as response:
            latency_ms = round((time.monotonic() - started) * 1000.0, 1)
            snippet = _read_snippet(response)
            return {
                "ok": True,
                "label": label,
                "url": url,
                "http_status": int(getattr(response, "status", 200) or 200),
                "latency_ms": latency_ms,
                "failure_class": "",
                "error": "",
                "response_snippet": snippet,
            }
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        detail = payload or str(exc)
        return {
            "ok": False,
            "label": label,
            "url": url,
            "http_status": int(exc.code),
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
            "failure_class": _classify_api_error("proxy_http_error", detail),
            "error": detail[:500],
            "response_snippet": detail[:240],
        }
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "label": label,
            "url": url,
            "http_status": 0,
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
            "failure_class": _classify_api_error("proxy_request_failed", detail),
            "error": detail,
            "response_snippet": "",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Webshare residential proxy transport")
    parser.add_argument("--http-url", default=DEFAULT_HTTP_URL, help="HTTP URL to fetch through proxy")
    parser.add_argument("--https-url", default=DEFAULT_HTTPS_URL, help="HTTPS URL to fetch through proxy (tests CONNECT)")
    parser.add_argument(
        "--youtube-url",
        default=DEFAULT_YOUTUBE_URL,
        help="Small YouTube HTTPS URL to fetch through proxy",
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="Per-probe timeout in seconds")
    parser.add_argument("--skip-http", action="store_true", help="Skip plain HTTP proxy probe")
    parser.add_argument("--skip-https", action="store_true", help="Skip HTTPS CONNECT probe")
    parser.add_argument("--skip-youtube", action="store_true", help="Skip YouTube HTTPS probe")
    parser.add_argument("--json", action="store_true", help="Reserved for compatibility; output is always JSON")
    args = parser.parse_args()

    _load_local_env()
    settings = _resolve_proxy_settings()

    report: dict[str, Any] = {
        "configured": bool(settings["username"] and settings["password"]),
        "proxy": {
            "host": settings["host"],
            "port": settings["port"],
            "locations": settings["locations"],
            "url_redacted": _redacted_proxy_url(settings),
            "warnings": settings["warnings"],
        },
        "probes": {},
    }

    if not report["configured"]:
        report["error"] = "proxy_not_configured"
        report["failure_class"] = "proxy_not_configured"
        report["message"] = "PROXY_USERNAME/PROXY_PASSWORD (or Webshare aliases) are not set."
        print(json.dumps(report, indent=2))
        return 1

    proxy_url = _proxy_url(settings)
    report["probes"]["tcp"] = _tcp_probe(
        str(settings["host"]),
        int(settings["port"]),
        timeout_seconds=float(args.timeout_seconds or 15.0),
    )

    if not args.skip_http:
        report["probes"]["http"] = _request_via_proxy(
            label="http",
            url=str(args.http_url),
            proxy_url=proxy_url,
            timeout_seconds=float(args.timeout_seconds or 15.0),
        )
    if not args.skip_https:
        report["probes"]["https_connect"] = _request_via_proxy(
            label="https_connect",
            url=str(args.https_url),
            proxy_url=proxy_url,
            timeout_seconds=float(args.timeout_seconds or 15.0),
        )
    if not args.skip_youtube:
        report["probes"]["youtube_https"] = _request_via_proxy(
            label="youtube_https",
            url=str(args.youtube_url),
            proxy_url=proxy_url,
            timeout_seconds=float(args.timeout_seconds or 15.0),
        )

    failures = [
        probe.get("failure_class")
        for probe in report["probes"].values()
        if isinstance(probe, dict) and not bool(probe.get("ok"))
    ]
    report["ok"] = not failures
    report["failure_classes"] = [str(item) for item in failures if item]

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
