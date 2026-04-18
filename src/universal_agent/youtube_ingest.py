from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

_YT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LOW_INFO_PHRASES = (
    "please like and subscribe",
    "thank you for watching",
    "thanks for watching",
    "see you in the next video",
)


def extract_video_id(video_url: str | None) -> Optional[str]:
    if not isinstance(video_url, str):
        return None
    raw = video_url.strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")

    if "youtu.be" in host and path:
        candidate = path.split("/", 1)[0]
        return candidate if _YT_VIDEO_ID_RE.fullmatch(candidate) else None

    if "youtube.com" in host:
        if path == "watch":
            values = parse_qs(parsed.query).get("v", [])
            if values and _YT_VIDEO_ID_RE.fullmatch(values[0]):
                return values[0]
        if path.startswith("shorts/"):
            candidate = path.split("/", 1)[1]
            return candidate if _YT_VIDEO_ID_RE.fullmatch(candidate) else None
        if path.startswith("live/"):
            candidate = path.split("/", 1)[1]
            return candidate if _YT_VIDEO_ID_RE.fullmatch(candidate) else None
    return None


def normalize_video_target(video_url: str | None, video_id: str | None) -> tuple[Optional[str], Optional[str]]:
    cleaned_id = (video_id or "").strip() or None
    if cleaned_id and not _YT_VIDEO_ID_RE.fullmatch(cleaned_id):
        cleaned_id = None

    cleaned_url = (video_url or "").strip() or None
    if not cleaned_id and cleaned_url:
        cleaned_id = extract_video_id(cleaned_url)
    if not cleaned_url and cleaned_id:
        cleaned_url = f"https://www.youtube.com/watch?v={cleaned_id}"
    return cleaned_url, cleaned_id


def _classify_api_error(error: str, detail: str) -> str:
    lowered = f"{error}\n{detail}".lower()
    if any(
        hint in lowered
        for hint in (
            "video is no longer available",
            "video unavailable",
            "video is unavailable",
            "this video is unavailable",
            "this video is private",
            "private video",
            "has been removed by the uploader",
            "video has been removed",
            "not available in your country",
        )
    ):
        return "video_unavailable"
    if any(
        hint in lowered
        for hint in (
            "subtitles are disabled",
            "transcripts are disabled",
            "no transcripts were found",
            "no transcript available",
            "no transcripts available",
            "transcript is not available",
        )
    ):
        return "transcript_unavailable"
    if any(
        hint in lowered
        for hint in (
            "invalid video id",
            "video id is invalid",
            "malformed video id",
        )
    ):
        return "invalid_video_target"
    if any(
        hint in lowered
        for hint in (
            "402 payment required",
            "payment required",
            "quota exceeded",
            "insufficient balance",
            "billing",
            "out of credits",
        )
    ):
        return "proxy_quota_or_billing"
    if any(
        hint in lowered
        for hint in (
            "no_proxies_allocated",
            "not in your proxy list anymore",
            "proxy list anymore",
            "auto-replacement rules enabled on the proxy settings page",
        )
    ):
        return "proxy_pool_unallocated"
    if any(
        hint in lowered
        for hint in (
            "407 proxy authentication required",
            "proxy auth",
            "proxy authentication",
            "invalid proxy credentials",
            "bad proxy credentials",
            "not authenticated or invalid authentication credentials",
            "invalid authentication credentials",
            "not authenticated",
        )
    ):
        return "proxy_auth_failed"
    if any(
        hint in lowered
        for hint in (
            "tunnel connection failed",
            "unable to connect to proxy",
            "proxyerror",
            "cannot connect to proxy",
            "proxy connection failed",
            "connect tunnel failed",
        )
    ):
        return "proxy_connect_failed"
    if any(
        hint in lowered
        for hint in (
            "requestblocked",
            "ipblocked",
            "ip has been blocked",
            "too many requests",
            "cloud provider",
            "sign in to confirm",
            "captcha",
            "429",
            "403",
        )
    ):
        return "request_blocked"
    return "api_unavailable"


def _evaluate_transcript_quality(transcript_text: str, min_chars: int) -> tuple[bool, float, str]:
    lines = [line.strip() for line in transcript_text.splitlines() if line.strip()]
    text = "\n".join(lines)
    chars = len(text)
    min_chars = max(20, min(int(min_chars or 0), 5000))
    if chars < min_chars:
        return False, 0.0, f"transcript shorter than minimum threshold ({chars} < {min_chars})"

    unique_ratio = 1.0
    if lines:
        unique_ratio = len(set(lines)) / float(len(lines))

    lowered = text.lower()
    low_info_phrase = any(phrase in lowered for phrase in _LOW_INFO_PHRASES)
    if low_info_phrase and chars < max(280, min_chars * 3):
        return False, 0.1, "transcript appears to contain sign-off boilerplate only"

    if unique_ratio < 0.15 and chars < max(500, min_chars * 4):
        return False, 0.15, "transcript appears low-information due to repeated content"

    score = min(1.0, 0.6 * min(chars / 6000.0, 1.0) + 0.4 * max(min(unique_ratio, 1.0), 0.0))
    return True, round(score, 4), ""


def _parse_proxy_locations(raw: str) -> list[str]:
    values = [part.strip().lower() for part in (raw or "").split(",") if part.strip()]
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _build_webshare_proxy_config() -> tuple[Optional[Any], str]:
    username = (
        os.getenv("PROXY_USERNAME")
        or os.getenv("WEBSHARE_PROXY_USER")
        or ""
    ).strip()
    password = (
        os.getenv("PROXY_PASSWORD")
        or os.getenv("WEBSHARE_PROXY_PASS")
        or ""
    ).strip()
    if not username or not password:
        return None, "disabled"

    try:
        from youtube_transcript_api.proxies import WebshareProxyConfig
    except Exception:
        return None, "module_unavailable"

    location_raw = (
        os.getenv("PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("PROXY_LOCATIONS")
        or os.getenv("YT_PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("WEBSHARE_PROXY_LOCATIONS")
        or ""
    )
    locations = _parse_proxy_locations(location_raw)
    domain_name = (
        os.getenv("WEBSHARE_PROXY_HOST")
        or os.getenv("PROXY_HOST")
        or "proxy.webshare.io"
    ).strip() or "proxy.webshare.io"
    proxy_port_raw = (
        os.getenv("WEBSHARE_PROXY_PORT")
        or os.getenv("PROXY_PORT")
        or "80"
    ).strip()
    try:
        proxy_port = int(proxy_port_raw)
    except Exception:
        proxy_port = 80
    if proxy_port <= 0 or proxy_port > 65535:
        proxy_port = 80
    kwargs: dict[str, Any] = {
        "proxy_username": username,
        "proxy_password": password,
        "domain_name": domain_name,
        "proxy_port": proxy_port,
    }
    if locations:
        kwargs["filter_ip_locations"] = locations
    return WebshareProxyConfig(**kwargs), "webshare"


def _build_dataimpulse_proxy_config() -> tuple[Optional[Any], str]:
    """Build a GenericProxyConfig for DataImpulse rotating residential proxies.

    Reads credentials from DATAIMPULSE_PROXY_USER / DATAIMPULSE_PROXY_PASS
    env vars (populated from Infisical).  DataImpulse uses standard HTTP
    proxy auth format: http://user:pass@host:port.

    Rotating HTTP/HTTPS port: 823 (default)
    SOCKS5 port:              824 (not used here)
    """
    username = (os.getenv("DATAIMPULSE_PROXY_USER") or "").strip()
    password = (os.getenv("DATAIMPULSE_PROXY_PASS") or "").strip()
    if not username or not password:
        return None, "disabled"

    try:
        from youtube_transcript_api.proxies import GenericProxyConfig
    except Exception:
        return None, "module_unavailable"

    host = (
        os.getenv("DATAIMPULSE_PROXY_HOST") or "gw.dataimpulse.com"
    ).strip() or "gw.dataimpulse.com"
    port_raw = (
        os.getenv("DATAIMPULSE_PROXY_PORT") or "823"
    ).strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 823
    if port <= 0 or port > 65535:
        port = 823

    proxy_url = f"http://{username}:{password}@{host}:{port}"
    return GenericProxyConfig(
        http_url=proxy_url,
        https_url=proxy_url,
    ), "dataimpulse"


def _build_proxy_config() -> tuple[Optional[Any], str]:
    """Route to the correct proxy builder based on PROXY_PROVIDER env var.

    Supported values:
      - "webshare"     (default) — Webshare.io via WebshareProxyConfig
      - "dataimpulse"  — DataImpulse via GenericProxyConfig
    """
    provider = (os.getenv("PROXY_PROVIDER") or "webshare").strip().lower()
    if provider == "dataimpulse":
        return _build_dataimpulse_proxy_config()
    return _build_webshare_proxy_config()


def _parse_iso8601_duration(raw: str) -> int | None:
    """Parse ISO 8601 duration (e.g. 'PT1H2M34S') to total seconds."""
    if not raw:
        return None
    m = re.match(
        r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$",
        raw.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _run_youtube_data_api_metadata(
    video_id: str,
    *,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    """Fetch video metadata via YouTube Data API v3.

    Uses YOUTUBE_API_KEY env var.  Returns ~2 KB JSON — no proxy needed,
    no HTML page scraping, no anti-bot risk.  This replaces the yt-dlp
    metadata path that was downloading ~500 KB of watch-page HTML through
    the expensive rotating residential proxy.
    """
    api_key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    if not api_key:
        return {
            "ok": False,
            "error": "youtube_api_key_missing",
            "detail": "YOUTUBE_API_KEY env var not set",
            "failure_class": "api_unavailable",
            "source": "youtube_data_api_v3",
        }

    url = (
        f"https://www.googleapis.com/youtube/v3/videos"
        f"?part=snippet,contentDetails,statistics"
        f"&id={video_id}"
        f"&key={api_key}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=max(5, min(timeout_seconds, 60))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "error": "youtube_data_api_request_failed",
            "detail": detail,
            "failure_class": _classify_api_error("youtube_data_api_request_failed", detail),
            "source": "youtube_data_api_v3",
        }

    items = data.get("items") or []
    if not items:
        return {
            "ok": False,
            "error": "youtube_data_api_no_results",
            "detail": f"No video found for id={video_id}",
            "failure_class": "video_unavailable",
            "source": "youtube_data_api_v3",
        }

    item = items[0]
    snippet = item.get("snippet") or {}
    content_details = item.get("contentDetails") or {}
    statistics = item.get("statistics") or {}

    duration_seconds = _parse_iso8601_duration(
        str(content_details.get("duration") or ""),
    )
    upload_date_raw = str(snippet.get("publishedAt") or "").strip()
    # Convert ISO 8601 date to YYYYMMDD for yt-dlp compat
    upload_date = None
    if upload_date_raw:
        try:
            upload_date = upload_date_raw[:10].replace("-", "")
        except Exception:
            upload_date = upload_date_raw

    metadata = {
        "title": str(snippet.get("title") or "").strip() or None,
        "channel": str(snippet.get("channelTitle") or "").strip() or None,
        "channel_id": str(snippet.get("channelId") or "").strip() or None,
        "upload_date": upload_date,
        "duration": duration_seconds,
        "view_count": int(statistics.get("viewCount") or 0) or None,
        "like_count": int(statistics.get("likeCount") or 0) or None,
        "description": str(snippet.get("description") or "").strip() or None,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
    }
    return {
        "ok": True,
        "source": "youtube_data_api_v3",
        "metadata": metadata,
    }


def _run_youtube_metadata_extract(
    video_id: str,
    *,
    proxy_url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Fetch metadata via yt-dlp (fallback).  Used only when YouTube Data API
    key is unavailable.  Runs WITHOUT proxy to avoid wasting residential
    bandwidth on metadata that doesn't need anti-bot evasion."""
    try:
        import yt_dlp
    except Exception as exc:
        return {
            "ok": False,
            "error": "yt_dlp_import_failed",
            "detail": str(exc),
            "failure_class": "api_unavailable",
            "source": "yt_dlp",
        }

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": max(5, min(int(timeout_seconds or 0), 600)),
    }
    # NOTE: proxy intentionally NOT set here.  Metadata fetch via yt-dlp
    # is a fallback path — we don't spend proxy bandwidth on it.  If it
    # gets blocked from a datacenter IP it will just fail gracefully.

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_id, download=False)
        metadata = {
            "title": str(info.get("title") or "").strip() or None,
            "channel": str(info.get("uploader") or "").strip() or None,
            "channel_id": str(info.get("channel_id") or "").strip() or None,
            "upload_date": str(info.get("upload_date") or "").strip() or None,
            "duration": int(info.get("duration") or 0) or None,
            "view_count": int(info.get("view_count") or 0) or None,
            "like_count": int(info.get("like_count") or 0) or None,
            "description": str(info.get("description") or "").strip() or None,
            "webpage_url": str(info.get("webpage_url") or "").strip() or None,
        }
        return {
            "ok": True,
            "source": "yt_dlp",
            "metadata": metadata,
        }
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "error": "yt_dlp_metadata_failed",
            "detail": detail,
            "failure_class": _classify_api_error("yt_dlp_metadata_failed", detail),
            "source": "yt_dlp",
        }


def _run_youtube_transcript_api_extract(
    video_id: str,
    *,
    language: str = "en",
    proxy_config: Optional[Any] = None,
    proxy_mode: str = "disabled",
) -> dict[str, Any]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "error": "youtube_transcript_api_import_failed",
            "detail": detail,
            "failure_class": "api_unavailable",
        }

    try:
        api_kwargs: dict[str, Any] = {}
        if proxy_config is not None:
            api_kwargs["proxy_config"] = proxy_config
        api = YouTubeTranscriptApi(**api_kwargs)
        lang = (language or "").strip().lower() or "en"
        preferred_languages = [lang] if lang != "en" else ["en"]
        if "en" not in preferred_languages:
            preferred_languages.append("en")
        fetched = api.fetch(video_id, languages=preferred_languages)
        lines: list[str] = []
        snippets = getattr(fetched, "snippets", None)
        if snippets is not None:
            for snippet in snippets:
                text = str(getattr(snippet, "text", "") or "").strip()
                if text:
                    lines.append(text)
        else:
            for item in fetched:
                text = str(item.get("text", "") or "").strip()
                if text:
                    lines.append(text)
        transcript_text = "\n".join(lines).strip()
        if not transcript_text:
            return {
                "ok": False,
                "error": "youtube_transcript_api_empty_transcript",
                "failure_class": "empty_or_low_quality_transcript",
            }
        return {
            "ok": True,
            "transcript_text": transcript_text,
            "source": "youtube_transcript_api",
            "language": lang,
            "proxy_mode": proxy_mode,
        }
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "error": "youtube_transcript_api_failed",
            "detail": detail,
            "failure_class": _classify_api_error("youtube_transcript_api_failed", detail),
        }


def ingest_youtube_transcript(
    *,
    video_url: str | None,
    video_id: str | None,
    language: str = "en",
    timeout_seconds: int = 120,
    max_chars: int = 180_000,
    min_chars: int = 160,
    require_proxy: bool = False,
) -> dict[str, Any]:
    resolved_url, resolved_video_id = normalize_video_target(video_url, video_id)
    if not resolved_url:
        return {
            "ok": False,
            "status": "failed",
            "error": "missing_video_target",
            "failure_class": "invalid_video_target",
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "attempts": [],
        }
    if not resolved_video_id:
        return {
            "ok": False,
            "status": "failed",
            "error": "invalid_video_target",
            "failure_class": "invalid_video_target",
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "attempts": [],
        }

    proxy_config, proxy_mode = _build_proxy_config()

    if require_proxy and proxy_config is None:
        _provider = (os.getenv("PROXY_PROVIDER") or "webshare").strip().lower()
        _reason = (
            "PROXY NOT CONFIGURED — YouTube transcript fetch BLOCKED. "
            "Residential proxy is REQUIRED to avoid IP bans on this server. "
        )
        if proxy_mode == "module_unavailable":
            _reason += (
                "The youtube_transcript_api.proxies module could not be imported. "
                "Install or upgrade youtube-transcript-api to a version that "
                "includes WebshareProxyConfig / GenericProxyConfig."
            )
        elif _provider == "dataimpulse":
            _reason += (
                "Set DATAIMPULSE_PROXY_USER and DATAIMPULSE_PROXY_PASS environment variables "
                "(DataImpulse residential proxy credentials). "
                "Without a residential proxy, requests from this server's datacenter IP WILL be blocked by YouTube."
            )
        else:
            _reason += (
                "Set PROXY_USERNAME and PROXY_PASSWORD environment variables "
                "(Webshare residential proxy credentials). "
                "Without a residential proxy, requests from this server's datacenter IP WILL be blocked by YouTube."
            )
        return {
            "ok": False,
            "status": "failed",
            "error": "proxy_not_configured",
            "failure_class": "proxy_not_configured",
            "detail": _reason,
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "proxy_mode": proxy_mode,
            "attempts": [],
        }

    # ── Metadata strategy: API-first, yt-dlp fallback ──────────────────
    # YouTube Data API v3 returns ~2 KB JSON (no proxy, no anti-bot).
    # yt-dlp scrapes ~500 KB HTML watch page — used only as fallback and
    # deliberately runs WITHOUT proxy to conserve residential bandwidth.
    metadata_result = _run_youtube_data_api_metadata(
        resolved_video_id, timeout_seconds=min(15, timeout_seconds),
    )
    if not metadata_result.get("ok"):
        log.debug(
            "YouTube Data API metadata failed for %s (%s), falling back to yt-dlp",
            resolved_video_id,
            metadata_result.get("error"),
        )
        metadata_result = _run_youtube_metadata_extract(
            resolved_video_id,
            proxy_url="",  # no proxy for fallback metadata
            timeout_seconds=timeout_seconds,
        )

    # ── Transcript fetch (always needs proxy for anti-bot) ────────────
    transcript_result = _run_youtube_transcript_api_extract(
        resolved_video_id,
        language=language,
        proxy_config=proxy_config,
        proxy_mode=proxy_mode,
    )

    attempts: list[dict[str, Any]] = []
    attempts.append({"method": "youtube_transcript_api", **transcript_result})
    attempts.append({"method": "metadata", **metadata_result})
    transcript_text = str(transcript_result.get("transcript_text") or "")
    source = str(transcript_result.get("source") or "youtube_transcript_api")
    metadata = metadata_result.get("metadata") if isinstance(metadata_result.get("metadata"), dict) else {}
    metadata_status = "attempted_succeeded" if metadata_result.get("ok") else "attempted_failed"
    metadata_source = str(metadata_result.get("source") or "youtube_data_api_v3")
    metadata_error = str(metadata_result.get("error") or "").strip()
    metadata_failure_class = str(metadata_result.get("failure_class") or "").strip()

    if not transcript_text:
        failure_error = str(transcript_result.get("error") or "transcript_unavailable").strip()
        failure_class = str(transcript_result.get("failure_class") or "api_unavailable").strip()
        return {
            "ok": False,
            "status": "failed",
            "error": failure_error or "transcript_unavailable",
            "failure_class": failure_class or "api_unavailable",
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "metadata": metadata,
            "metadata_status": metadata_status,
            "metadata_source": metadata_source,
            "metadata_error": metadata_error or None,
            "metadata_failure_class": metadata_failure_class or None,
            "attempts": attempts,
        }

    max_chars = max(1_000, min(int(max_chars or 0), 800_000))
    truncated = False
    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars]
        truncated = True

    quality_pass, quality_score, quality_reason = _evaluate_transcript_quality(
        transcript_text=transcript_text,
        min_chars=min_chars,
    )
    if not quality_pass:
        return {
            "ok": False,
            "status": "failed",
            "error": "transcript_quality_failed",
            "failure_class": "empty_or_low_quality_transcript",
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "source": source,
            "transcript_chars": len(transcript_text),
            "transcript_truncated": truncated,
            "transcript_quality_score": quality_score,
            "transcript_quality_reason": quality_reason,
            "metadata": metadata,
            "metadata_status": metadata_status,
            "metadata_source": metadata_source,
            "metadata_error": metadata_error or None,
            "metadata_failure_class": metadata_failure_class or None,
            "attempts": attempts,
        }

    return {
        "ok": True,
        "status": "succeeded",
        "video_url": resolved_url,
        "video_id": resolved_video_id,
        "transcript_text": transcript_text,
        "transcript_chars": len(transcript_text),
        "transcript_truncated": truncated,
        "source": source or "unknown",
        "proxy_mode": proxy_mode,
        "transcript_quality_score": quality_score,
        "transcript_quality_pass": True,
        "metadata": metadata,
        "metadata_status": metadata_status,
        "metadata_source": metadata_source,
        "metadata_error": metadata_error or None,
        "metadata_failure_class": metadata_failure_class or None,
        "attempts": attempts,
    }
