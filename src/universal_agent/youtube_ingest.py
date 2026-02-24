from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

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
    username = (os.getenv("PROXY_USERNAME") or "").strip()
    password = (os.getenv("PROXY_PASSWORD") or "").strip()
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
        or ""
    )
    locations = _parse_proxy_locations(location_raw)
    kwargs: dict[str, Any] = {
        "proxy_username": username,
        "proxy_password": password,
    }
    if locations:
        kwargs["filter_ip_locations"] = locations
    return WebshareProxyConfig(**kwargs), "webshare"


def _run_youtube_transcript_api_extract(video_id: str) -> dict[str, Any]:
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
        proxy_config, proxy_mode = _build_webshare_proxy_config()
        api_kwargs: dict[str, Any] = {}
        if proxy_config is not None:
            api_kwargs["proxy_config"] = proxy_config
        api = YouTubeTranscriptApi(**api_kwargs)
        fetched = api.fetch(video_id, languages=["en"])
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
            "language": "en",
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

    attempts: list[dict[str, Any]] = []
    transcript_result = _run_youtube_transcript_api_extract(resolved_video_id)
    attempts.append({"method": "youtube_transcript_api", **transcript_result})
    transcript_text = str(transcript_result.get("transcript_text") or "")
    source = str(transcript_result.get("source") or "youtube_transcript_api")

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
        "transcript_quality_score": quality_score,
        "transcript_quality_pass": True,
        "attempts": attempts,
    }
