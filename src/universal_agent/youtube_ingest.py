from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


_SRT_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}")
_YT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


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


def _srt_to_text(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.isdigit():
            continue
        if _SRT_TIME_RE.match(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _run_yt_dlp_extract(video_url: str, language: str, timeout_seconds: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ua_yt_ingest_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        output_tpl = str(tmp_path / "subs.%(ext)s")
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-auto-subs",
            "--sub-lang",
            language,
            "--convert-subs",
            "srt",
            "-o",
            output_tpl,
            video_url,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(5, int(timeout_seconds)),
            check=False,
        )
        srt_files = sorted(tmp_path.glob("*.srt"))
        if not srt_files:
            stderr_preview = (proc.stderr or proc.stdout or "").strip()
            return {
                "ok": False,
                "error": "yt_dlp_no_srt_output",
                "detail": stderr_preview[-2000:],
                "returncode": proc.returncode,
            }

        raw = srt_files[0].read_text(encoding="utf-8", errors="replace")
        cleaned = _srt_to_text(raw)
        if not cleaned:
            return {"ok": False, "error": "yt_dlp_empty_transcript", "returncode": proc.returncode}
        return {
            "ok": True,
            "transcript_text": cleaned,
            "source": "yt_dlp_srt",
            "returncode": proc.returncode,
        }


def _run_youtube_transcript_api_extract(video_id: str) -> dict[str, Any]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as exc:
        return {"ok": False, "error": "youtube_transcript_api_import_failed", "detail": str(exc)}

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
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
            return {"ok": False, "error": "youtube_transcript_api_empty_transcript"}
        return {
            "ok": True,
            "transcript_text": transcript_text,
            "source": "youtube_transcript_api",
        }
    except Exception as exc:
        return {"ok": False, "error": "youtube_transcript_api_failed", "detail": str(exc)}


def ingest_youtube_transcript(
    *,
    video_url: str | None,
    video_id: str | None,
    language: str = "en",
    timeout_seconds: int = 120,
    max_chars: int = 180_000,
) -> dict[str, Any]:
    resolved_url, resolved_video_id = normalize_video_target(video_url, video_id)
    if not resolved_url:
        return {
            "ok": False,
            "status": "failed",
            "error": "missing_video_target",
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "attempts": [],
        }

    attempts: list[dict[str, Any]] = []
    transcript_text = ""
    source = ""

    yt_dlp_result = _run_yt_dlp_extract(resolved_url, language=language, timeout_seconds=timeout_seconds)
    attempts.append({"method": "yt_dlp", **yt_dlp_result})
    if yt_dlp_result.get("ok"):
        transcript_text = str(yt_dlp_result.get("transcript_text") or "")
        source = str(yt_dlp_result.get("source") or "yt_dlp_srt")
    elif resolved_video_id:
        fallback_result = _run_youtube_transcript_api_extract(resolved_video_id)
        attempts.append({"method": "youtube_transcript_api", **fallback_result})
        if fallback_result.get("ok"):
            transcript_text = str(fallback_result.get("transcript_text") or "")
            source = str(fallback_result.get("source") or "youtube_transcript_api")

    if not transcript_text:
        failure_error = ""
        for attempt in reversed(attempts):
            failure_error = str(attempt.get("error") or "").strip()
            if failure_error:
                break
        return {
            "ok": False,
            "status": "failed",
            "error": failure_error or "transcript_unavailable",
            "video_url": resolved_url,
            "video_id": resolved_video_id,
            "attempts": attempts,
        }

    max_chars = max(1_000, min(int(max_chars or 0), 800_000))
    truncated = False
    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars]
        truncated = True

    return {
        "ok": True,
        "status": "succeeded",
        "video_url": resolved_url,
        "video_id": resolved_video_id,
        "transcript_text": transcript_text,
        "transcript_chars": len(transcript_text),
        "transcript_truncated": truncated,
        "source": source or "unknown",
        "attempts": attempts,
    }
