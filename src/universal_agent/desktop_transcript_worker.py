"""Desktop Transcript Worker — proxy-free YouTube transcript fetching.

Runs on the desktop (residential IP) to avoid proxy costs.
Falls back to rotating WebShare proxy if direct fetch fails.

This is a STANDALONE PROGRAM — not part of the UA cron system.
Run it via systemd timer, cron, or manually.

Usage:
    # Test mode — fetch specific videos, no VPS interaction
    uv run python src/universal_agent/desktop_transcript_worker.py \\
        --test daPwd4DnEfA avXA9Jgi-WE etPbMbx7rP0

    # Batch mode — pull failed transcripts from VPS CSI DB, fetch locally,
    # write results back
    uv run python src/universal_agent/desktop_transcript_worker.py --batch

    # Dry-run batch — show what WOULD be processed without doing it
    uv run python src/universal_agent/desktop_transcript_worker.py --batch --dry-run

Toggle:
    Set DESKTOP_TRANSCRIPT_WORKER_ENABLED=false to disable (default: true)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("desktop_transcript_worker")

# ── Loud banner for visibility ────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          DESKTOP TRANSCRIPT WORKER                          ║
║          Residential IP — zero proxy transcript fetch       ║
╚══════════════════════════════════════════════════════════════╝
"""


# ── Failure classification ────────────────────────────────────────────────


class FailureType(str, Enum):
    """Loud, distinct failure categories so operators know EXACTLY what
    happened.  These are logged at CRITICAL or WARNING level with explicit
    banners — you cannot miss them."""

    NONE = "none"

    # Self-imposed limits (our own config, not YouTube)
    CAP_BATCH_SIZE = "SELF_IMPOSED_CAP:batch_size"
    CAP_DAILY_LIMIT = "SELF_IMPOSED_CAP:daily_limit"
    CAP_WORKER_DISABLED = "SELF_IMPOSED_CAP:worker_disabled"

    # YouTube / network blocks
    YOUTUBE_BOT_DETECTION = "YOUTUBE_BLOCK:bot_detection"
    YOUTUBE_RATE_LIMITED = "YOUTUBE_BLOCK:rate_limited"
    YOUTUBE_VIDEO_UNAVAILABLE = "YOUTUBE_BLOCK:video_unavailable"
    YOUTUBE_CAPTIONS_DISABLED = "YOUTUBE_BLOCK:captions_disabled"
    YOUTUBE_UNKNOWN_ERROR = "YOUTUBE_BLOCK:unknown_error"

    # Circuit breaker (triggered by consecutive failures)
    CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER:tripped"
    CIRCUIT_BREAKER_ABORT = "CIRCUIT_BREAKER:abort_batch"

    # Infrastructure
    NETWORK_ERROR = "INFRA:network_error"
    SSH_ERROR = "INFRA:ssh_error"
    DB_ERROR = "INFRA:db_error"
    IMPORT_ERROR = "INFRA:import_error"


def _classify_error(error_msg: str) -> FailureType:
    """Classify a youtube-transcript-api error into a loud failure type."""
    e = error_msg.lower()
    if "sign in" in e or "bot" in e or "confirm" in e:
        return FailureType.YOUTUBE_BOT_DETECTION
    if "429" in e or "too many" in e or "rate" in e:
        return FailureType.YOUTUBE_RATE_LIMITED
    if "unavailable" in e or "not exist" in e or "private" in e or "unplayable" in e:
        return FailureType.YOUTUBE_VIDEO_UNAVAILABLE
    if ("subtitles" in e and "disabled" in e) or "no transcript" in e:
        return FailureType.YOUTUBE_CAPTIONS_DISABLED
    if "timeout" in e or "connection" in e or "ssl" in e:
        return FailureType.NETWORK_ERROR
    if "no module" in e or "import" in e:
        return FailureType.IMPORT_ERROR
    return FailureType.YOUTUBE_UNKNOWN_ERROR


def _loud_log(failure_type: FailureType, message: str, **kwargs: Any) -> None:
    """Log failures with extremely visible formatting."""
    prefix = failure_type.value

    if "SELF_IMPOSED_CAP" in prefix:
        log.critical(
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║  ⚠️  SELF-IMPOSED CAP HIT — NOT A YOUTUBE BLOCK        ║\n"
            "╠══════════════════════════════════════════════════════════╣\n"
            "║  Type: %-48s ║\n"
            "║  %s\n"
            "╚══════════════════════════════════════════════════════════╝",
            prefix,
            message,
        )
    elif "YOUTUBE_BLOCK" in prefix:
        log.critical(
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║  🚫 YOUTUBE BLOCK DETECTED                              ║\n"
            "╠══════════════════════════════════════════════════════════╣\n"
            "║  Type: %-48s ║\n"
            "║  %s\n"
            "╚══════════════════════════════════════════════════════════╝",
            prefix,
            message,
        )
    elif "CIRCUIT_BREAKER" in prefix:
        log.warning(
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║  🔌 CIRCUIT BREAKER                                     ║\n"
            "╠══════════════════════════════════════════════════════════╣\n"
            "║  Type: %-48s ║\n"
            "║  %s\n"
            "╚══════════════════════════════════════════════════════════╝",
            prefix,
            message,
        )
    else:
        log.error("[%s] %s", prefix, message)


# ── Configuration ─────────────────────────────────────────────────────────


VPS_HOST = "root@srv1360701"
CSI_DB_PATH = "/var/lib/universal-agent/csi/csi.db"


@dataclass
class WorkerConfig:
    """All knobs for the desktop transcript worker."""

    enabled: bool = True

    # Rate limiting
    delay_between_requests: float = 5.0  # seconds between transcript fetches
    batch_size: int = 25  # max videos per batch run
    daily_cap: int = 200  # max videos per day (safety)

    # Circuit breaker
    max_consecutive_failures: int = 3  # after N fails, pause
    circuit_breaker_cooldown: float = 60.0  # seconds to wait after breaker
    max_circuit_breaker_trips: int = 2  # abort batch after N trips

    proxy_fallback_enabled: bool = False

    # Transcript params
    language: str = "en"

    # VPS connection
    vps_host: str = VPS_HOST
    csi_db_path: str = CSI_DB_PATH

    @classmethod
    def from_env(cls) -> WorkerConfig:
        """Load config from environment variables."""
        return cls(
            enabled=os.getenv("DESKTOP_TRANSCRIPT_WORKER_ENABLED", "true")
            .lower()
            not in ("false", "0", "no", "off"),
            delay_between_requests=float(
                os.getenv("DTW_DELAY_SECONDS", "5.0")
            ),
            batch_size=int(os.getenv("DTW_BATCH_SIZE", "25")),
            daily_cap=int(os.getenv("DTW_DAILY_CAP", "200")),
            max_consecutive_failures=int(
                os.getenv("DTW_MAX_CONSECUTIVE_FAILURES", "3")
            ),
            circuit_breaker_cooldown=float(
                os.getenv("DTW_CIRCUIT_BREAKER_COOLDOWN", "60.0")
            ),
            max_circuit_breaker_trips=int(
                os.getenv("DTW_MAX_CIRCUIT_BREAKER_TRIPS", "2")
            ),
            proxy_fallback_enabled=os.getenv("DTW_PROXY_FALLBACK", "false")
            .lower()
            not in ("false", "0", "no", "off"),
            language=os.getenv("DTW_LANGUAGE", "en"),
            vps_host=os.getenv("DTW_VPS_HOST", VPS_HOST),
            csi_db_path=os.getenv("DTW_CSI_DB_PATH", CSI_DB_PATH),
        )


# ── Result objects ────────────────────────────────────────────────────────


@dataclass
class TranscriptResult:
    """Result of a single transcript fetch."""

    video_id: str
    ok: bool
    transcript_text: str = ""
    source: str = ""  # "local" or "proxy_fallback"
    char_count: int = 0
    elapsed_seconds: float = 0.0
    error: str = ""
    failure_type: FailureType = FailureType.NONE
    method: str = ""

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "ok": self.ok,
            "source": self.source,
            "char_count": self.char_count,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "error": self.error,
            "failure_type": self.failure_type.value,
            "method": self.method,
        }


@dataclass
class BatchResult:
    """Result of a batch transcript run."""

    total_requested: int = 0
    total_processed: int = 0
    success_local: int = 0
    success_proxy: int = 0
    failed: int = 0
    skipped_cap: int = 0  # skipped due to self-imposed caps
    skipped_other: int = 0
    circuit_breaker_trips: int = 0
    total_chars: int = 0
    total_elapsed: float = 0.0
    abort_reason: str = ""
    results: list[TranscriptResult] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"Requested={self.total_requested}",
            f"Processed={self.total_processed}",
            f"✅ local={self.success_local}",
            f"✅ proxy={self.success_proxy}",
            f"❌ failed={self.failed}",
        ]
        if self.skipped_cap > 0:
            parts.append(f"⚠️  SKIPPED_BY_CAP={self.skipped_cap}")
        if self.skipped_other > 0:
            parts.append(f"skipped_other={self.skipped_other}")
        parts.extend([
            f"chars={self.total_chars:,}",
            f"time={self.total_elapsed:.1f}s",
        ])
        if self.circuit_breaker_trips > 0:
            parts.append(
                f"🔌 breaker_trips={self.circuit_breaker_trips}"
            )
        if self.abort_reason:
            parts.append(f"ABORT: {self.abort_reason}")
        return " | ".join(parts)


# ── Core fetch logic ─────────────────────────────────────────────────────


def _fetch_transcript_local(
    video_id: str, *, language: str = "en"
) -> TranscriptResult:
    """Fetch transcript directly without proxy (residential IP)."""
    t0 = time.time()
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        preferred = [language] if language != "en" else ["en"]
        if "en" not in preferred:
            preferred.append("en")

        fetched = api.fetch(video_id, languages=preferred)
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
        elapsed = time.time() - t0

        if not transcript_text:
            return TranscriptResult(
                video_id=video_id,
                ok=False,
                error="empty_transcript",
                elapsed_seconds=elapsed,
                source="local",
                failure_type=FailureType.YOUTUBE_CAPTIONS_DISABLED,
                method="youtube_transcript_api",
            )

        return TranscriptResult(
            video_id=video_id,
            ok=True,
            transcript_text=transcript_text,
            char_count=len(transcript_text),
            elapsed_seconds=elapsed,
            source="local",
            failure_type=FailureType.NONE,
            method="youtube_transcript_api",
        )
    except Exception as exc:
        elapsed = time.time() - t0
        error_str = str(exc)[:300]
        return TranscriptResult(
            video_id=video_id,
            ok=False,
            error=error_str,
            elapsed_seconds=elapsed,
            source="local",
            failure_type=_classify_error(error_str),
            method="youtube_transcript_api",
        )


def _fetch_transcript_proxy(
    video_id: str, *, language: str = "en"
) -> TranscriptResult:
    """Fetch transcript via WebShare rotating proxy (fallback)."""
    t0 = time.time()
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.proxies import WebshareProxyConfig

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
            elapsed = time.time() - t0
            return TranscriptResult(
                video_id=video_id,
                ok=False,
                error="no_proxy_credentials_in_env",
                elapsed_seconds=elapsed,
                source="proxy_fallback",
                failure_type=FailureType.NETWORK_ERROR,
                method="youtube_transcript_api",
            )

        domain_name = (
            os.getenv("WEBSHARE_PROXY_HOST")
            or os.getenv("PROXY_HOST")
            or "proxy.webshare.io"
        ).strip()
        proxy_port_raw = (
            os.getenv("WEBSHARE_PROXY_PORT")
            or os.getenv("PROXY_PORT")
            or "80"
        ).strip()
        try:
            proxy_port = int(proxy_port_raw)
        except Exception:
            proxy_port = 80

        proxy_config = WebshareProxyConfig(
            proxy_username=username,
            proxy_password=password,
            domain_name=domain_name,
            proxy_port=proxy_port,
        )

        api = YouTubeTranscriptApi(proxy_config=proxy_config)
        preferred = [language] if language != "en" else ["en"]
        if "en" not in preferred:
            preferred.append("en")

        fetched = api.fetch(video_id, languages=preferred)
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
        elapsed = time.time() - t0

        if not transcript_text:
            return TranscriptResult(
                video_id=video_id,
                ok=False,
                error="empty_transcript_via_proxy",
                elapsed_seconds=elapsed,
                source="proxy_fallback",
                failure_type=FailureType.YOUTUBE_CAPTIONS_DISABLED,
                method="youtube_transcript_api",
            )

        return TranscriptResult(
            video_id=video_id,
            ok=True,
            transcript_text=transcript_text,
            char_count=len(transcript_text),
            elapsed_seconds=elapsed,
            source="proxy_fallback",
            failure_type=FailureType.NONE,
            method="youtube_transcript_api",
        )
    except Exception as exc:
        elapsed = time.time() - t0
        error_str = str(exc)[:300]
        return TranscriptResult(
            video_id=video_id,
            ok=False,
            error=error_str,
            elapsed_seconds=elapsed,
            source="proxy_fallback",
            failure_type=_classify_error(error_str),
            method="youtube_transcript_api",
        )


# ── VPS SSH integration ──────────────────────────────────────────────────


def _ssh_run(host: str, command: str, *, timeout: int = 30) -> str:
    """Run a command on VPS via SSH. Returns stdout."""
    try:
        result = subprocess.run(
            ["ssh", host, command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"SSH command failed (rc={result.returncode}): "
                f"{result.stderr[:300]}"
            )
        return result.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"SSH command timed out after {timeout}s")
    except FileNotFoundError:
        raise RuntimeError("SSH binary not found — is openssh installed?")


def fetch_pending_video_ids(
    config: WorkerConfig, *, limit: int = 50
) -> list[dict]:
    """Query VPS CSI DB for videos that need transcripts.

    Returns list of dicts with video_id, event_id, title.
    """
    # Target: transcript_status='failed' with a valid video_id
    sql = (
        f"SELECT event_id, video_id, title, channel_name "
        f"FROM rss_event_analysis "
        f"WHERE transcript_status = 'failed' "
        f"AND video_id IS NOT NULL AND video_id != '' "
        f"ORDER BY analyzed_at DESC "
        f"LIMIT {limit};"
    )
    try:
        raw = _ssh_run(
            config.vps_host,
            f'sqlite3 -json {config.csi_db_path} "{sql}"',
            timeout=15,
        )
        if not raw.strip():
            return []
        rows = json.loads(raw)
        return [
            {
                "event_id": r.get("event_id", ""),
                "video_id": r.get("video_id", ""),
                "title": r.get("title", ""),
                "channel_name": r.get("channel_name", ""),
            }
            for r in rows
            if r.get("video_id")
        ]
    except json.JSONDecodeError as e:
        _loud_log(
            FailureType.DB_ERROR,
            f"Failed to parse VPS DB response as JSON: {e}",
        )
        return []
    except RuntimeError as e:
        _loud_log(FailureType.SSH_ERROR, str(e))
        return []


def write_transcript_to_vps(
    config: WorkerConfig,
    event_id: str,
    transcript_text: str,
    char_count: int,
) -> bool:
    """Write a fetched transcript back to the VPS CSI DB."""
    # Escape single quotes for SQL
    safe_text = transcript_text.replace("'", "''")
    # Store transcript as a ref (truncated for the DB field)
    transcript_ref = f"desktop_worker_{int(time.time())}"

    sql = (
        f"UPDATE rss_event_analysis SET "
        f"transcript_status = 'ok', "
        f"transcript_chars = {char_count}, "
        f"transcript_ref = '{transcript_ref}' "
        f"WHERE event_id = '{event_id}';"
    )
    # Retry up to 3 times for DB lock contention
    for attempt in range(3):
        try:
            _ssh_run(
                config.vps_host,
                f'sqlite3 {config.csi_db_path} "{sql}"',
                timeout=10,
            )
            return True
        except RuntimeError as e:
            if "locked" in str(e).lower() and attempt < 2:
                log.warning(
                    "DB locked on write for %s, retrying in 2s (attempt %d/3)",
                    event_id,
                    attempt + 1,
                )
                time.sleep(2)
                continue
            _loud_log(
                FailureType.DB_ERROR,
                f"Failed to write transcript for {event_id}: {e}",
            )
            return False
    return False


# ── Batch processor ──────────────────────────────────────────────────────


def process_batch(
    video_ids: list[str],
    config: Optional[WorkerConfig] = None,
    *,
    event_ids: Optional[dict[str, str]] = None,
) -> BatchResult:
    """Process a batch of video IDs with rate limiting and circuit breaker.

    Tries local (no proxy) first.  Falls back to proxy if enabled.
    Respects rate limits, circuit breaker, and daily cap.

    Args:
        video_ids: List of YouTube video IDs to process.
        config: Worker config (reads from env if None).
        event_ids: Optional mapping of video_id -> event_id for VPS writeback.
    """
    cfg = config or WorkerConfig.from_env()
    batch = BatchResult(total_requested=len(video_ids))
    eid_map = event_ids or {}

    # ── Pre-flight checks ─────────────────────────────────────────────
    if not cfg.enabled:
        _loud_log(
            FailureType.CAP_WORKER_DISABLED,
            f"Worker is DISABLED via DESKTOP_TRANSCRIPT_WORKER_ENABLED=false. "
            f"{len(video_ids)} videos will NOT be processed. "
            f"Set DESKTOP_TRANSCRIPT_WORKER_ENABLED=true to re-enable.",
        )
        batch.skipped_cap = len(video_ids)
        batch.abort_reason = "WORKER_DISABLED"
        return batch

    # Apply batch size cap
    if len(video_ids) > cfg.batch_size:
        overflow = len(video_ids) - cfg.batch_size
        _loud_log(
            FailureType.CAP_BATCH_SIZE,
            f"Requested {len(video_ids)} videos but batch_size={cfg.batch_size}. "
            f"{overflow} videos WILL NOT be processed in this batch. "
            f"They will be picked up in the next run. "
            f"Increase DTW_BATCH_SIZE to process more per batch.",
        )
        batch.skipped_cap = overflow

    effective_ids = video_ids[: cfg.batch_size]

    # Daily cap check (simplified — in production, track via state file)
    if len(effective_ids) > cfg.daily_cap:
        overflow = len(effective_ids) - cfg.daily_cap
        _loud_log(
            FailureType.CAP_DAILY_LIMIT,
            f"Batch of {len(effective_ids)} exceeds daily_cap={cfg.daily_cap}. "
            f"{overflow} videos WILL NOT be processed today. "
            f"Increase DTW_DAILY_CAP if this is intentional growth.",
        )
        batch.skipped_cap += overflow
        effective_ids = effective_ids[: cfg.daily_cap]

    batch.total_processed = len(effective_ids)
    consecutive_failures = 0
    total_breaker_trips = 0
    t_batch_start = time.time()

    for i, video_id in enumerate(effective_ids):
        video_id = video_id.strip()
        if not video_id:
            batch.skipped_other += 1
            continue

        # ── Circuit breaker check ─────────────────────────────────────
        if consecutive_failures >= cfg.max_consecutive_failures:
            total_breaker_trips += 1
            batch.circuit_breaker_trips = total_breaker_trips

            if total_breaker_trips > cfg.max_circuit_breaker_trips:
                _loud_log(
                    FailureType.CIRCUIT_BREAKER_ABORT,
                    f"Circuit breaker tripped {total_breaker_trips} times "
                    f"(max={cfg.max_circuit_breaker_trips}). "
                    f"ABORTING BATCH — {len(effective_ids) - i} videos "
                    f"remaining will NOT be processed. "
                    f"This likely means YouTube is blocking requests. "
                    f"Check if your residential IP has been rate-limited.",
                )
                batch.abort_reason = (
                    f"CIRCUIT_BREAKER_ABORT after {total_breaker_trips} trips"
                )
                batch.skipped_other += len(effective_ids) - i
                break

            _loud_log(
                FailureType.CIRCUIT_BREAKER_TRIPPED,
                f"Circuit breaker tripped (trip #{total_breaker_trips}) "
                f"after {consecutive_failures} consecutive failures. "
                f"Cooling down for {cfg.circuit_breaker_cooldown:.0f}s... "
                f"({cfg.max_circuit_breaker_trips - total_breaker_trips} "
                f"trips remaining before abort)",
            )
            time.sleep(cfg.circuit_breaker_cooldown)
            consecutive_failures = 0

        # ── Rate limiting delay ───────────────────────────────────────
        if i > 0:
            time.sleep(cfg.delay_between_requests)

        # ── Step 1: Try local (no proxy) ──────────────────────────────
        log.info(
            "[%d/%d] %s — fetching locally (no proxy)...",
            i + 1,
            batch.total_processed,
            video_id,
        )
        result = _fetch_transcript_local(video_id, language=cfg.language)

        # ── Step 2: Proxy fallback if local failed ────────────────────
        if not result.ok and cfg.proxy_fallback_enabled:
            log.info(
                "  Local failed [%s]. Trying proxy fallback...",
                result.failure_type.value,
            )
            time.sleep(1)
            result = _fetch_transcript_proxy(video_id, language=cfg.language)

        # ── Record result ─────────────────────────────────────────────
        batch.results.append(result)
        if result.ok:
            consecutive_failures = 0
            batch.total_chars += result.char_count
            if result.source == "local":
                batch.success_local += 1
            else:
                batch.success_proxy += 1
            log.info(
                "  ✅ %s: %d chars via %s in %.1fs",
                video_id,
                result.char_count,
                result.source,
                result.elapsed_seconds,
            )
        else:
            consecutive_failures += 1
            batch.failed += 1
            _loud_log(
                result.failure_type,
                f"Video {video_id} FAILED: {result.error[:150]}",
            )

    batch.total_elapsed = time.time() - t_batch_start
    return batch


# ── CLI entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    """CLI for the desktop transcript worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        print("Environment variables:")
        print("  DESKTOP_TRANSCRIPT_WORKER_ENABLED  (default: true)")
        print("  DTW_DELAY_SECONDS                  (default: 5.0)")
        print("  DTW_BATCH_SIZE                     (default: 25)")
        print("  DTW_DAILY_CAP                      (default: 200)")
        print("  DTW_MAX_CONSECUTIVE_FAILURES       (default: 3)")
        print("  DTW_CIRCUIT_BREAKER_COOLDOWN       (default: 60.0)")
        print("  DTW_MAX_CIRCUIT_BREAKER_TRIPS      (default: 2)")
        print("  DTW_PROXY_FALLBACK                 (default: false)")
        print("  DTW_LANGUAGE                       (default: en)")
        print("  DTW_VPS_HOST                       (default: root@srv1360701)")
        print("  DTW_CSI_DB_PATH                    (default: "
              "/var/lib/universal-agent/csi/csi.db)")
        sys.exit(0)

    config = WorkerConfig.from_env()

    # ── Test mode ─────────────────────────────────────────────────────
    if args[0] == "--test":
        video_ids = args[1:]
        if not video_ids:
            print("ERROR: --test requires video IDs")
            sys.exit(1)

        print(BANNER)
        print(f"MODE: TEST (standalone, no VPS interaction)")
        print(f"Videos: {len(video_ids)}")
        print(f"Delay: {config.delay_between_requests}s")
        print(f"Proxy fallback: {config.proxy_fallback_enabled}")
        print(f"Batch size: {config.batch_size}")
        print(f"Daily cap: {config.daily_cap}")
        print("=" * 60)
        print()

        result = process_batch(video_ids, config=config)
        _print_results(result)

    # ── Batch mode — VPS integration ──────────────────────────────────
    elif args[0] == "--batch":
        dry_run = "--dry-run" in args
        print(BANNER)
        print(f"MODE: BATCH {'(DRY RUN)' if dry_run else '(LIVE)'}")
        print(f"VPS: {config.vps_host}")
        print(f"DB: {config.csi_db_path}")
        print(f"Batch size: {config.batch_size}")
        print(f"Delay: {config.delay_between_requests}s")
        print(f"Proxy fallback: {config.proxy_fallback_enabled}")
        print("=" * 60)
        print()

        # Step 1: Fetch pending videos from VPS
        log.info("Querying VPS for videos with failed transcripts...")
        pending = fetch_pending_video_ids(config, limit=config.batch_size)

        if not pending:
            log.info("No pending videos found. Nothing to do.")
            sys.exit(0)

        log.info("Found %d videos needing transcripts:", len(pending))
        for p in pending[:10]:  # Preview first 10
            log.info(
                "  %s — %s (%s)",
                p["video_id"],
                (p["title"] or "?")[:50],
                p["channel_name"] or "?",
            )
        if len(pending) > 10:
            log.info("  ... and %d more", len(pending) - 10)

        if dry_run:
            log.info("DRY RUN — would process %d videos. Exiting.", len(pending))
            sys.exit(0)

        # Build event_id mapping for writeback
        video_ids = [p["video_id"] for p in pending]
        eid_map = {p["video_id"]: p["event_id"] for p in pending}

        # Step 2: Fetch transcripts locally
        result = process_batch(
            video_ids, config=config, event_ids=eid_map
        )

        # Step 3: Write successful transcripts back to VPS
        written = 0
        write_failed = 0
        for r in result.results:
            if r.ok and r.video_id in eid_map:
                event_id = eid_map[r.video_id]
                ok = write_transcript_to_vps(
                    config, event_id, r.transcript_text, r.char_count
                )
                if ok:
                    written += 1
                    log.info(
                        "  📤 Wrote %s → VPS (event=%s, %d chars)",
                        r.video_id,
                        event_id[:20],
                        r.char_count,
                    )
                else:
                    write_failed += 1

        # Summary
        log.info("")
        log.info("VPS WRITEBACK: %d written, %d failed", written, write_failed)
        _print_results(result)

    else:
        print(f"Unknown command: {args[0]}")
        print("Use --test, --batch, or --help")
        sys.exit(1)


def _print_results(result: BatchResult) -> None:
    """Print final results summary."""
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(result.summary())
    print()

    for r in result.results:
        status = "✅" if r.ok else "❌"
        if r.ok:
            preview = r.transcript_text[:80].replace("\n", " ") + "..."
            print(
                f"  {status} {r.video_id} [{r.source}]"
                f" {r.char_count:,} chars {r.elapsed_seconds:.1f}s"
            )
            print(f"     {preview}")
        else:
            print(
                f"  {status} {r.video_id} [{r.failure_type.value}]"
                f" {r.elapsed_seconds:.1f}s"
            )
            print(f"     {r.error[:120]}")

    if result.skipped_cap > 0:
        print()
        print(
            f"  ⚠️  {result.skipped_cap} videos SKIPPED due to "
            f"self-imposed caps (NOT YouTube blocks)"
        )

    if result.abort_reason:
        print()
        print(f"  🛑 BATCH ABORTED: {result.abort_reason}")

    # Write JSON results
    results_path = "/tmp/dtw_results.json"
    with open(results_path, "w") as f:
        json.dump(
            {
                "summary": result.summary(),
                "total_requested": result.total_requested,
                "total_processed": result.total_processed,
                "success_local": result.success_local,
                "success_proxy": result.success_proxy,
                "failed": result.failed,
                "skipped_cap": result.skipped_cap,
                "abort_reason": result.abort_reason,
                "results": [r.to_dict() for r in result.results],
            },
            f,
            indent=2,
        )
    print(f"\nFull results: {results_path}")


if __name__ == "__main__":
    main()
