#!/usr/bin/env python3
"""Batch new RSS events and send a periodic digest to Telegram."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.categories import canonicalize_category, format_category_label
from csi_ingester.store import token_usage as token_usage_store


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _parse_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _get_from_env_file(path: Path, keys: list[str]) -> str:
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            item = line.strip()
            if not item or item.startswith("#") or "=" not in item:
                continue
            key, raw_val = item.split("=", 1)
            key = key.strip()
            if key in keys:
                val = _parse_dotenv_value(raw_val)
                if val:
                    return val
    except Exception:
        return ""
    return ""


def _resolve_setting(keys: list[str], env_files: list[Path]) -> str:
    for key in keys:
        val = os.getenv(key, "").strip()
        if val:
            return val
    for env_file in env_files:
        found = _get_from_env_file(env_file, keys).strip()
        if found:
            return found
    return ""


def _resolve_chat_id(env_files: list[Path]) -> str:
    chat_id = _resolve_setting(
        ["CSI_RSS_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID", "TELEGRAM_DEFAULT_CHAT_ID"],
        env_files,
    )
    if chat_id:
        return chat_id

    raw_allowed = _resolve_setting(["TELEGRAM_ALLOWED_USER_IDS"], env_files)
    if raw_allowed:
        first = raw_allowed.split(",", 1)[0].strip()
        if first:
            return first
    return ""


def _build_fallback_digest(
    rows: list[sqlite3.Row],
    *,
    max_items: int,
    window_label: str,
) -> str:
    channel_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    theme_counts: Counter[str] = Counter()
    items: list[dict[str, str]] = []

    for row in rows:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        channel_name = str(subject.get("channel_name") or "").strip()
        channel_id = str(subject.get("channel_id") or "").strip()
        channel_label = channel_name or channel_id or "unknown-channel"
        channel_counts[channel_label] += 1

        video_id = str(subject.get("video_id") or "").strip()
        video_url = str(subject.get("url") or "").strip()
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"

        title = str(subject.get("title") or "").strip() or "(untitled)"
        created_at = str(row["created_at"] or "")
        raw_category = canonicalize_category(str(row["category"] or ""))
        category_counts[raw_category] += 1
        try:
            analysis_payload = json.loads(str(row["analysis_json"] or "{}"))
            if isinstance(analysis_payload, dict):
                themes = analysis_payload.get("themes")
                if isinstance(themes, list):
                    for theme in themes:
                        label = str(theme).strip().lower()
                        if label:
                            theme_counts[label] += 1
        except Exception:
            pass

        items.append(
            {
                "channel": channel_label,
                "title": title.replace("\n", " "),
                "url": video_url,
                "created_at": created_at,
                "category": raw_category,
            }
        )

    first_ts = str(rows[0]["created_at"] or "")
    last_ts = str(rows[-1]["created_at"] or "")
    lines: list[str] = []
    lines.append(f"YouTube RSS Digest ({window_label})")
    lines.append(f"New videos: {len(rows)}")
    lines.append(
        "Category mix: "
        f"AI={category_counts.get('ai', 0)} | "
        f"Political={category_counts.get('political', 0)} | "
        f"War={category_counts.get('war', 0)} | "
        f"Other={category_counts.get('other_interest', 0)}"
    )
    extras = [
        f"{format_category_label(slug)}={count}"
        for slug, count in sorted(category_counts.items())
        if slug not in {"ai", "political", "war", "other_interest"}
    ]
    if extras:
        lines.append(f"Emerging mix: {' | '.join(extras[:6])}")
    lines.append(f"Window: {first_ts} -> {last_ts}")
    lines.append("")
    lines.append("Top channels:")
    for channel, count in channel_counts.most_common(5):
        lines.append(f"- {channel}: {count}")
    lines.append("")
    lines.append("Watchlist movers:")
    for channel, count in channel_counts.most_common(5):
        lines.append(f"- {channel}: +{count}")
    if theme_counts:
        lines.append("")
        lines.append("Top narratives:")
        for theme, count in theme_counts.most_common(6):
            lines.append(f"- {theme}: {count}")
    lines.append("")
    lines.append("Videos by category:")

    buckets: dict[str, list[dict[str, str]]] = {}
    for item in items:
        key = item.get("category", "other_interest")
        buckets.setdefault(key, []).append(item)

    emitted = 0
    max_emit = max(1, max_items)
    ordered_base = ["ai", "political", "war", "other_interest"]
    ordered = [(slug, format_category_label(slug)) for slug in ordered_base if buckets.get(slug)]
    extras_in_order = [
        (slug, format_category_label(slug))
        for slug, _count in sorted(
            [(key, len(value)) for key, value in buckets.items() if key not in ordered_base],
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    ordered.extend(extras_in_order)
    non_empty = [(k, l) for (k, l) in ordered if buckets.get(k)]
    quotas: dict[str, int] = {k: 0 for k, _ in ordered}
    used_per_bucket: dict[str, int] = {k: 0 for k, _ in ordered}

    if non_empty:
        base = max_emit // len(non_empty)
        remainder = max_emit % len(non_empty)
        for idx, (key, _) in enumerate(non_empty):
            quotas[key] = base + (1 if idx < remainder else 0)
            if quotas[key] == 0:
                quotas[key] = 1

    for key, label in non_empty:
        lines.append(f"{label}:")
        quota = quotas[key]
        for item in buckets[key][:quota]:
            if emitted >= max_emit:
                break
            lines.append(f"- {item['channel']}: {item['title']}")
            if item["url"]:
                lines.append(f"  {item['url']}")
            emitted += 1
            used_per_bucket[key] += 1

    if emitted < max_emit:
        for key, _label in ordered:
            if emitted >= max_emit:
                break
            start = used_per_bucket[key]
            for item in buckets[key][start:]:
                if emitted >= max_emit:
                    break
                lines.append(f"- {item['channel']}: {item['title']}")
                if item["url"]:
                    lines.append(f"  {item['url']}")
                emitted += 1

    if len(items) > emitted:
        lines.append(f"- ... and {len(items) - emitted} more")

    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3897] + "..."
    return msg


def _maybe_build_claude_digest(
    rows: list[sqlite3.Row],
    *,
    max_items: int,
    window_label: str,
    env_files: list[Path],
) -> tuple[str | None, dict[str, int]]:
    use_claude = os.getenv("CSI_RSS_DIGEST_USE_CLAUDE", "0").strip() == "1"
    if not use_claude:
        return None, {}

    api_key = _resolve_setting(["ANTHROPIC_API_KEY"], env_files)
    if not api_key:
        return None, {}

    model = os.getenv("CSI_RSS_DIGEST_CLAUDE_MODEL", "claude-3-5-haiku-latest").strip()
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url

    compact_events: list[dict[str, str]] = []
    for row in rows[: max(1, max_items * 3)]:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}
        compact_events.append(
            {
                "created_at": str(row["created_at"] or ""),
                "channel_name": str(subject.get("channel_name") or ""),
                "channel_id": str(subject.get("channel_id") or ""),
                "title": str(subject.get("title") or ""),
                "video_id": str(subject.get("video_id") or ""),
                "url": str(subject.get("url") or ""),
                "category": canonicalize_category(str(row["category"] or "")),
                "analysis_json": str(row["analysis_json"] or "{}")[:1200],
            }
        )

    prompt = (
        "Create a concise Telegram digest for newly detected YouTube videos.\n"
        f"Time bucket: {window_label}\n"
        f"Total events: {len(rows)}\n"
        f"Include at most {max_items} video bullets.\n"
        "Rules:\n"
        "- Plain text only (no markdown)\n"
        "- Keep under 3000 characters\n"
        "- Include explicit sections: AI, Political, War, Other Interest, Emerging Categories\n"
        "- Group related items when useful\n"
        "- Focus on channel + video title + URL\n\n"
        f"Events JSON:\n{json.dumps(compact_events, ensure_ascii=False)}"
    )
    body = {
        "model": model,
        "max_tokens": 700,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, {}

    content = data.get("content")
    if not isinstance(content, list):
        return None, {}
    text_parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or "").strip())
    out = "\n".join(part for part in text_parts if part).strip()
    if not out:
        return None, {}
    if len(out) > 3900:
        out = out[:3897] + "..."

    usage_obj = data.get("usage")
    usage: dict[str, int] = {}
    if isinstance(usage_obj, dict):
        input_tokens = int(usage_obj.get("input_tokens") or 0)
        cache_create = int(usage_obj.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage_obj.get("cache_read_input_tokens") or 0)
        prompt_tokens = max(0, input_tokens + cache_create + cache_read)
        completion_tokens = int(usage_obj.get("output_tokens") or 0)
        total_tokens = int(usage_obj.get("total_tokens") or (prompt_tokens + completion_tokens))
        usage = {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
        }
    return out, usage


def _send_telegram_message(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if not data.get("ok"):
                return False, f"telegram_api_not_ok response={raw[:400]}"
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return False, f"telegram_http_error status={exc.code} body={body[:400]}"
    except Exception as exc:
        return False, f"telegram_send_exception type={type(exc).__name__} message={exc}"
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send 10-minute batched RSS digest to Telegram.")
    parser.add_argument("--db-path", required=True, help="Path to CSI sqlite db")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/rss_telegram_digest_state.json",
        help="Path to persisted digest cursor state",
    )
    parser.add_argument(
        "--source",
        default="youtube_channel_rss",
        help="Event source to aggregate (default: youtube_channel_rss)",
    )
    parser.add_argument("--max-items", type=int, default=12, help="Max video bullets in one digest")
    parser.add_argument("--window-label", default="10m", help="Label for digest window")
    parser.add_argument("--chat-id", default="", help="Telegram chat id override")
    parser.add_argument("--bot-token", default="", help="Telegram bot token override")
    parser.add_argument(
        "--env-file",
        default="/opt/universal_agent/.env",
        help="Fallback env file for Telegram/Anthropic keys",
    )
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
        help="Additional env file for CSI-specific overrides",
    )
    parser.add_argument(
        "--seed-current-on-first-run",
        action="store_true",
        help="When no state exists, set cursor to current max id and do not notify.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print digest but do not send Telegram")
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"RSS_TELEGRAM_DB_MISSING path={db_path}")
        return 2

    state_path = Path(args.state_path).expanduser()
    env_files: list[Path] = []
    if args.env_file:
        env_files.append(Path(args.env_file).expanduser())
    if args.csi_env_file:
        env_files.append(Path(args.csi_env_file).expanduser())
    state = _load_state(state_path)

    conn = _connect(db_path)
    source = args.source
    max_row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE source = ?", (source,)).fetchone()
    max_id = int(max_row[0] or 0)

    if "last_sent_id" not in state and args.seed_current_on_first_run:
        _save_state(state_path, {"last_sent_id": max_id})
        conn.close()
        print(f"RSS_TELEGRAM_SEEDED_LAST_SENT_ID={max_id}")
        return 0

    last_sent_id = int(state.get("last_sent_id") or 0)
    rows = conn.execute(
        """
        SELECT
            e.id,
            e.event_id,
            e.created_at,
            e.delivered,
            e.subject_json,
            COALESCE(a.category, 'other_interest') AS category,
            COALESCE(a.analysis_json, '{}') AS analysis_json
        FROM events e
        LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
        WHERE e.source = ? AND e.delivered = 1 AND e.id > ?
        ORDER BY e.id ASC
        """,
        (source, last_sent_id),
    ).fetchall()
    conn.close()

    print(f"RSS_TELEGRAM_LAST_SENT_ID={last_sent_id}")
    print(f"RSS_TELEGRAM_NEW_COUNT={len(rows)}")
    if not rows:
        return 0

    chat_id = (args.chat_id or "").strip() or _resolve_chat_id(env_files)
    bot_token = (args.bot_token or "").strip() or _resolve_setting(
        ["CSI_RSS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"], env_files
    )

    if not chat_id:
        print("RSS_TELEGRAM_SKIPPED_CHAT_ID_MISSING (set CSI_RSS_TELEGRAM_CHAT_ID)")
        return 0
    if not bot_token:
        print("RSS_TELEGRAM_SKIPPED_BOT_TOKEN_MISSING (set CSI_RSS_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN)")
        return 0

    digest, usage = _maybe_build_claude_digest(
        rows,
        max_items=max(1, int(args.max_items)),
        window_label=args.window_label,
        env_files=env_files,
    )
    if digest is None:
        digest = _build_fallback_digest(
            rows,
            max_items=max(1, int(args.max_items)),
            window_label=args.window_label,
        )
    elif usage:
        conn_usage = _connect(db_path)
        try:
            token_usage_store.insert_usage(
                conn_usage,
                process_name="rss_digest_claude",
                model_name=os.getenv("CSI_RSS_DIGEST_CLAUDE_MODEL", "claude-3-5-haiku-latest").strip(),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                metadata={
                    "source": source,
                    "window_label": args.window_label,
                    "event_count": len(rows),
                },
            )
        finally:
            conn_usage.close()

    next_last_sent_id = int(rows[-1]["id"])
    if args.dry_run:
        print("RSS_TELEGRAM_DRY_RUN=1")
        print("---- DIGEST BEGIN ----")
        print(digest)
        print("---- DIGEST END ----")
        print(f"RSS_TELEGRAM_NEXT_LAST_SENT_ID={next_last_sent_id}")
        return 0

    ok, reason = _send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=digest)
    if not ok:
        print(f"RSS_TELEGRAM_SEND_FAILED reason={reason}")
        return 4

    _save_state(state_path, {"last_sent_id": next_last_sent_id})
    print("RSS_TELEGRAM_SENT=1")
    print(f"RSS_TELEGRAM_NEXT_LAST_SENT_ID={next_last_sent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
