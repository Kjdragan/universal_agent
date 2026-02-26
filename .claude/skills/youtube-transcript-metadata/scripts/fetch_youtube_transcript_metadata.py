#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-dotenv>=1.0.1",
#   "youtube-transcript-api>=1.2.0",
#   "yt-dlp>=2025.1.0",
# ]
# ///

from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from dotenv import find_dotenv, load_dotenv

YT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LOW_INFO_PHRASES = (
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
        return candidate if YT_VIDEO_ID_RE.fullmatch(candidate) else None

    if "youtube.com" in host:
        if path == "watch":
            values = parse_qs(parsed.query).get("v", [])
            if values and YT_VIDEO_ID_RE.fullmatch(values[0]):
                return values[0]
        if path.startswith("shorts/"):
            candidate = path.split("/", 1)[1]
            return candidate if YT_VIDEO_ID_RE.fullmatch(candidate) else None
        if path.startswith("live/"):
            candidate = path.split("/", 1)[1]
            return candidate if YT_VIDEO_ID_RE.fullmatch(candidate) else None
    return None


def normalize_video_target(video_url: str | None, video_id: str | None) -> tuple[Optional[str], Optional[str]]:
    cleaned_id = (video_id or "").strip() or None
    if cleaned_id and not YT_VIDEO_ID_RE.fullmatch(cleaned_id):
        cleaned_id = None

    cleaned_url = (video_url or "").strip() or None
    if not cleaned_id and cleaned_url:
        cleaned_id = extract_video_id(cleaned_url)
    if not cleaned_url and cleaned_id:
        cleaned_url = f"https://www.youtube.com/watch?v={cleaned_id}"
    return cleaned_url, cleaned_id


def classify_api_error(error: str, detail: str) -> str:
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


def evaluate_transcript_quality(transcript_text: str, min_chars: int) -> tuple[bool, float, str]:
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
    low_info_phrase = any(phrase in lowered for phrase in LOW_INFO_PHRASES)
    if low_info_phrase and chars < max(280, min_chars * 3):
        return False, 0.1, "transcript appears to contain sign-off boilerplate only"

    if unique_ratio < 0.15 and chars < max(500, min_chars * 4):
        return False, 0.15, "transcript appears low-information due to repeated content"

    score = min(1.0, 0.6 * min(chars / 6000.0, 1.0) + 0.4 * max(min(unique_ratio, 1.0), 0.0))
    return True, round(score, 4), ""


def parse_proxy_locations(raw: str) -> list[str]:
    values = [part.strip().lower() for part in (raw or "").split(",") if part.strip()]
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def build_webshare_proxy_config() -> tuple[Optional[Any], str, str]:
    username = (os.getenv("PROXY_USERNAME") or os.getenv("WEBSHARE_PROXY_USER") or "").strip()
    password = (os.getenv("PROXY_PASSWORD") or os.getenv("WEBSHARE_PROXY_PASS") or "").strip()
    if not username or not password:
        return None, "disabled", ""

    try:
        from youtube_transcript_api.proxies import WebshareProxyConfig
    except Exception:
        return None, "module_unavailable", ""

    location_raw = (
        os.getenv("PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("PROXY_LOCATIONS")
        or os.getenv("YT_PROXY_FILTER_IP_LOCATIONS")
        or os.getenv("WEBSHARE_PROXY_LOCATIONS")
        or ""
    )
    locations = parse_proxy_locations(location_raw)
    kwargs: dict[str, Any] = {
        "proxy_username": username,
        "proxy_password": password,
    }
    if locations:
        kwargs["filter_ip_locations"] = locations

    cfg = WebshareProxyConfig(**kwargs)
    proxy_url = str(getattr(cfg, "url", "") or "")
    return cfg, "webshare", proxy_url


def run_youtube_transcript_api_extract(
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
            "failure_class": classify_api_error("youtube_transcript_api_failed", detail),
        }


def run_youtube_metadata_extract(
    video_id: str,
    *,
    proxy_url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        import yt_dlp
    except Exception as exc:
        detail = str(exc)
        return {
            "ok": False,
            "error": "yt_dlp_import_failed",
            "detail": detail,
            "failure_class": "api_unavailable",
            "source": "yt_dlp",
        }

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": max(5, min(int(timeout_seconds or 0), 600)),
    }
    if proxy_url:
        ydl_opts["proxy"] = proxy_url

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
            "failure_class": classify_api_error("yt_dlp_metadata_failed", detail),
            "source": "yt_dlp",
        }


def fetch_transcript_and_metadata(
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

    proxy_config, proxy_mode, proxy_url = build_webshare_proxy_config()

    with ThreadPoolExecutor(max_workers=2) as executor:
        transcript_future = executor.submit(
            run_youtube_transcript_api_extract,
            resolved_video_id,
            language=language,
            proxy_config=proxy_config,
            proxy_mode=proxy_mode,
        )
        metadata_future = executor.submit(
            run_youtube_metadata_extract,
            resolved_video_id,
            proxy_url=proxy_url,
            timeout_seconds=timeout_seconds,
        )
        transcript_result = transcript_future.result()
        metadata_result = metadata_future.result()

    attempts: list[dict[str, Any]] = []
    attempts.append({"method": "youtube_transcript_api", **transcript_result})
    attempts.append({"method": "yt_dlp_metadata", **metadata_result})

    transcript_text = str(transcript_result.get("transcript_text") or "")
    source = str(transcript_result.get("source") or "youtube_transcript_api")
    metadata = metadata_result.get("metadata") if isinstance(metadata_result.get("metadata"), dict) else {}
    metadata_status = "attempted_succeeded" if metadata_result.get("ok") else "attempted_failed"
    metadata_source = str(metadata_result.get("source") or "yt_dlp")
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

    quality_pass, quality_score, quality_reason = evaluate_transcript_quality(
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
        "transcript_quality_score": quality_score,
        "transcript_quality_pass": True,
        "metadata": metadata,
        "metadata_status": metadata_status,
        "metadata_source": metadata_source,
        "metadata_error": metadata_error or None,
        "metadata_failure_class": metadata_failure_class or None,
        "attempts": attempts,
    }


def write_text(path: str, text: str) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def self_test() -> int:
    try:
        import yt_dlp  # noqa: F401
        from youtube_transcript_api import YouTubeTranscriptApi  # noqa: F401
    except Exception as exc:
        print(f"SELF_TEST_FAIL: {exc}")
        return 1

    print("SELF_TEST_OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch YouTube transcript + metadata in parallel.")
    parser.add_argument("--url", help="YouTube video URL")
    parser.add_argument("--video-id", help="YouTube video id")
    parser.add_argument("--language", default="en", help="Preferred transcript language (default: en)")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-chars", type=int, default=180_000)
    parser.add_argument("--min-chars", type=int, default=160)
    parser.add_argument("--json-out", help="Optional output JSON path")
    parser.add_argument("--transcript-out", help="Optional transcript text output path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout")
    parser.add_argument("--self-test", action="store_true", help="Run import checks only")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    load_dotenv(find_dotenv(usecwd=True))

    if not (args.url or args.video_id):
        print("Missing --url or --video-id")
        return 2

    result = fetch_transcript_and_metadata(
        video_url=args.url,
        video_id=args.video_id,
        language=args.language,
        timeout_seconds=args.timeout_seconds,
        max_chars=args.max_chars,
        min_chars=args.min_chars,
    )

    if args.transcript_out and isinstance(result.get("transcript_text"), str):
        write_text(args.transcript_out, str(result.get("transcript_text") or ""))

    payload = json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.json_out:
        write_text(args.json_out, payload + "\n")
    print(payload)

    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
