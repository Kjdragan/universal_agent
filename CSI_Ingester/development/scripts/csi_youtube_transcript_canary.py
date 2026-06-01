#!/usr/bin/env python3
"""YouTube transcript pipeline freshness canary.

Runs on a systemd timer. Checks csi.db for two regression classes:

  1. STALE: rss_event_analysis has had no fresh writes in the last
     `--stale-after-hours` hours despite YouTube events arriving. Catches
     "the enrichment timer is disabled / failing" — the exact regression
     that silently broke transcript ingestion for ~53 days in 2026-03/05.

  2. AUTH BROKEN: of the recently-analyzed rows, the
     `transcript_status='ok'` rate sat below `--min-ok-rate` and the
     `failed | transcript_ref like '%http_error%'` rate sat above
     `--max-http-error-rate`. Catches the empty-Authorization-header
     class of regression.

When either check fires, the canary exits non-zero AND posts a single
plain-text message to the CSI RSS Telegram channel (via direct Bot API
call). Telegram creds resolve from env-file → Infisical, mirroring the
enrichment script's auth pattern.

Run with `--no-alert` to suppress Telegram (used by unit tests and
operator dry-runs). Run with `--require-min-events N` to skip checks
during quiet windows (default 5).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _csi_secret_resolver import resolve_token_from_infisical  # noqa: E402


def compute_metrics(
    conn: sqlite3.Connection,
    *,
    window_hours: int,
    stale_after_hours: int,
) -> dict[str, int | float | None]:
    """Pull raw counts from the DB. Pure function over a sqlite connection."""
    cur = conn.cursor()
    events_recent = cur.execute(
        """
        SELECT COUNT(*) FROM events
        WHERE source='youtube_channel_rss'
          AND created_at >= datetime('now', ?)
        """,
        (f"-{window_hours} hours",),
    ).fetchone()[0]

    analyzed_recent = cur.execute(
        """
        SELECT COUNT(*) FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        """,
        (f"-{window_hours} hours",),
    ).fetchone()[0]

    ok_recent = cur.execute(
        """
        SELECT COUNT(*) FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
          AND transcript_status='ok'
        """,
        (f"-{window_hours} hours",),
    ).fetchone()[0]

    http_error_recent = cur.execute(
        """
        SELECT COUNT(*) FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
          AND transcript_status='failed'
          AND transcript_ref LIKE '%http_error%'
        """,
        (f"-{window_hours} hours",),
    ).fetchone()[0]

    last_analyzed = cur.execute(
        "SELECT MAX(analyzed_at) FROM rss_event_analysis"
    ).fetchone()[0]

    last_analyzed_age_hours = cur.execute(
        "SELECT (julianday('now') - julianday(MAX(analyzed_at))) * 24.0 "
        "FROM rss_event_analysis"
    ).fetchone()[0]

    return {
        "window_hours": window_hours,
        "stale_after_hours": stale_after_hours,
        "events_recent": int(events_recent or 0),
        "analyzed_recent": int(analyzed_recent or 0),
        "ok_recent": int(ok_recent or 0),
        "http_error_recent": int(http_error_recent or 0),
        "last_analyzed_at": last_analyzed,
        "last_analyzed_age_hours": (
            float(last_analyzed_age_hours)
            if last_analyzed_age_hours is not None
            else None
        ),
    }


def evaluate(
    metrics: dict[str, int | float | None],
    *,
    min_ok_rate: float,
    max_http_error_rate: float,
    require_min_events: int,
) -> dict[str, object]:
    """Return {'status': 'green'|'yellow'|'red', 'reasons': [...], 'rates': {...}}."""
    events = int(metrics["events_recent"])
    analyzed = int(metrics["analyzed_recent"])
    ok = int(metrics["ok_recent"])
    http_err = int(metrics["http_error_recent"])
    age = metrics["last_analyzed_age_hours"]
    stale_threshold = int(metrics["stale_after_hours"])

    reasons: list[str] = []
    rates = {
        "ok_rate": (ok / analyzed) if analyzed else None,
        "http_error_rate": (http_err / analyzed) if analyzed else None,
    }

    if events < require_min_events:
        return {
            "status": "green",
            "reasons": [
                f"quiet_window events_recent={events}<{require_min_events}"
            ],
            "rates": rates,
        }

    if age is None or age > stale_threshold:
        reasons.append(
            "stale rss_event_analysis"
            f" last_analyzed={metrics['last_analyzed_at']!r}"
            f" age_hours={age}"
            f" threshold={stale_threshold}h"
        )

    if analyzed == 0:
        reasons.append(
            f"no analyzed rows in window despite events_recent={events}"
        )
    else:
        if rates["ok_rate"] is not None and rates["ok_rate"] < min_ok_rate:
            reasons.append(
                f"ok_rate={rates['ok_rate']:.2f} < min_ok_rate={min_ok_rate}"
                f" (ok={ok}/analyzed={analyzed})"
            )
        if (
            rates["http_error_rate"] is not None
            and rates["http_error_rate"] > max_http_error_rate
        ):
            reasons.append(
                f"http_error_rate={rates['http_error_rate']:.2f}"
                f" > max_http_error_rate={max_http_error_rate}"
                f" (http_err={http_err}/analyzed={analyzed})"
            )

    return {
        "status": "red" if reasons else "green",
        "reasons": reasons,
        "rates": rates,
    }


def _telegram_post(*, bot_token: str, chat_id: str, thread_id: str, text: str) -> bool:
    if not bot_token or not chat_id:
        print("CANARY_TELEGRAM_SKIP missing_creds")
        return False
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            pass
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            ok = json.loads(body).get("ok") is True
            print(f"CANARY_TELEGRAM_POSTED ok={ok}")
            return ok
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"CANARY_TELEGRAM_POST_FAIL detail={exc!r}")
        return False


def _resolve_telegram_creds() -> tuple[str, str, str]:
    bot = (
        os.getenv("CSI_RSS_TELEGRAM_BOT_TOKEN", "").strip()
        or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    chat = os.getenv("CSI_RSS_TELEGRAM_CHAT_ID", "").strip()
    thread = os.getenv("CSI_RSS_TELEGRAM_THREAD_ID", "").strip()

    if not bot or not chat:
        # Fall back to Infisical exactly like the enrichment script's token chain.
        token_from_infisical = resolve_token_from_infisical(
            ["CSI_RSS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
            log_prefix="CANARY",
        )
        if not bot:
            bot = token_from_infisical
        if not chat:
            # Reuse resolver — it also returns chat IDs since they live in same
            # Infisical project.
            chat = resolve_token_from_infisical(
                ["CSI_RSS_TELEGRAM_CHAT_ID"], log_prefix="CANARY"
            )
        if not thread:
            thread = resolve_token_from_infisical(
                ["CSI_RSS_TELEGRAM_THREAD_ID"], log_prefix="CANARY"
            )

    return bot, chat, thread


def format_alert(metrics: dict[str, int | float | None], verdict: dict[str, object]) -> str:
    rates = verdict["rates"] or {}
    reasons = verdict["reasons"] or []
    ok_rate = rates.get("ok_rate")
    http_rate = rates.get("http_error_rate")
    return (
        "🚨 CSI YouTube transcript pipeline RED\n"
        f"window={metrics['window_hours']}h\n"
        f"events_recent={metrics['events_recent']}\n"
        f"analyzed_recent={metrics['analyzed_recent']}\n"
        f"ok_recent={metrics['ok_recent']}"
        f" ({ok_rate:.0%})\n" if ok_rate is not None else
        f"ok_recent={metrics['ok_recent']}\n"
    ) + (
        f"http_error_recent={metrics['http_error_recent']}"
        f" ({http_rate:.0%})\n" if http_rate is not None else
        f"http_error_recent={metrics['http_error_recent']}\n"
    ) + (
        f"last_analyzed_at={metrics['last_analyzed_at']}\n"
        f"last_analyzed_age_hours={metrics['last_analyzed_age_hours']}\n"
        "reasons:\n  - " + "\n  - ".join(reasons)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument("--window-hours", type=int, default=24)
    # Default 6h spans one full analyzer interval (csi-rss-semantic-enrich runs
    # every 4h) plus headroom; 2h false-RED'd in the gaps between runs.
    parser.add_argument("--stale-after-hours", type=int, default=6)
    parser.add_argument("--min-ok-rate", type=float, default=0.25)
    parser.add_argument("--max-http-error-rate", type=float, default=0.50)
    parser.add_argument("--require-min-events", type=int, default=5)
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Suppress Telegram posting (still exits non-zero on RED).",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"CANARY_FAIL db_missing path={db_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    try:
        metrics = compute_metrics(
            conn,
            window_hours=args.window_hours,
            stale_after_hours=args.stale_after_hours,
        )
    finally:
        conn.close()

    verdict = evaluate(
        metrics,
        min_ok_rate=args.min_ok_rate,
        max_http_error_rate=args.max_http_error_rate,
        require_min_events=args.require_min_events,
    )

    for key, value in metrics.items():
        print(f"CANARY_METRIC {key}={value}")
    print(f"CANARY_STATUS {verdict['status']}")
    for reason in verdict["reasons"] or []:
        print(f"CANARY_REASON {reason}")

    if verdict["status"] == "red":
        if not args.no_alert:
            bot, chat, thread = _resolve_telegram_creds()
            _telegram_post(
                bot_token=bot,
                chat_id=chat,
                thread_id=thread,
                text=format_alert(metrics, verdict),
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
