#!/usr/bin/env python3
"""Batch new Reddit discovery events and send a periodic digest to Telegram."""

from __future__ import annotations

import argparse
import json
import os
import re
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

from csi_ingester.store import token_usage as token_usage_store

CORE_ORDER = ("ai", "political", "war", "other_interest")

AI_HINTS = {
    "ai",
    "artificial",
    "machinelearning",
    "ml",
    "localllama",
    "llm",
    "chatgpt",
    "openai",
    "anthropic",
    "gemini",
    "language_model",
    "generative",
}
WAR_HINTS = {
    "war",
    "warcollege",
    "military",
    "combat",
    "battle",
    "defense",
    "ukrainewar",
    "geopoliticswar",
}
POLITICAL_HINTS = {
    "politics",
    "political",
    "geopolitics",
    "election",
    "government",
    "policy",
    "congress",
    "senate",
    "diplomacy",
}


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
        [
            "CSI_REDDIT_TELEGRAM_CHAT_ID",
            "CSI_TELEGRAM_CHAT_ID_REDDIT",
            "TELEGRAM_CHAT_ID_REDDIT",
            "CSI_RSS_TELEGRAM_CHAT_ID",
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_DEFAULT_CHAT_ID",
        ],
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


def _slugify(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower())
    out = re.sub(r"_+", "_", out).strip("_")
    return out


def _canonicalize_category(raw: str) -> str:
    slug = _slugify(raw)
    if slug in {"ai", "artificial_intelligence"}:
        return "ai"
    if slug in {"political", "politics", "geopolitics"}:
        return "political"
    if slug in {"war", "military", "conflict"}:
        return "war"
    if slug in {"other", "other_interest", "otherinterest", "misc"}:
        return "other_interest"
    if slug in CORE_ORDER:
        return slug
    return "other_interest"


def _format_category(slug: str) -> str:
    if slug == "ai":
        return "AI"
    if slug == "political":
        return "Political"
    if slug == "war":
        return "War"
    if slug == "other_interest":
        return "Other Interest"
    return slug.replace("_", " ").title() if slug else "Other Interest"


def _load_watchlist_category_map(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return mapping

    items: list[Any] = []
    if isinstance(payload, dict):
        maybe = payload.get("subreddits")
        if isinstance(maybe, list):
            items = maybe
    elif isinstance(payload, list):
        items = payload

    for row in items:
        if isinstance(row, str):
            name = row.strip()
            if name:
                mapping[name.lower()] = "other_interest"
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("subreddit") or "").strip()
        if not name:
            continue
        hint = _canonicalize_category(str(row.get("category_hint") or "other_interest"))
        mapping[name.lower()] = hint
    return mapping


def _infer_category(*, subreddit: str, title: str, watchlist_map: dict[str, str]) -> str:
    sub_l = subreddit.strip().lower()
    if sub_l in watchlist_map:
        return _canonicalize_category(watchlist_map[sub_l])

    text = f"{subreddit} {title}".lower()
    if any(hint in text for hint in AI_HINTS):
        return "ai"
    if any(hint in text for hint in WAR_HINTS):
        return "war"
    if any(hint in text for hint in POLITICAL_HINTS):
        return "political"
    return "other_interest"


def _build_fallback_digest(
    rows: list[sqlite3.Row],
    *,
    max_items: int,
    window_label: str,
    watchlist_map: dict[str, str],
) -> str:
    subreddit_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    items: list[dict[str, Any]] = []

    for row in rows:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        subreddit = str(subject.get("subreddit") or "unknown").strip() or "unknown"
        title = str(subject.get("title") or "(untitled)").strip() or "(untitled)"
        permalink = str(subject.get("permalink") or "").strip()
        url = str(subject.get("url") or "").strip()
        final_url = permalink or url
        score = int(subject.get("score") or 0)
        comments = int(subject.get("num_comments") or 0)
        category = _infer_category(subreddit=subreddit, title=title, watchlist_map=watchlist_map)

        subreddit_counts[subreddit] += 1
        category_counts[category] += 1
        items.append(
            {
                "subreddit": subreddit,
                "title": title.replace("\n", " "),
                "url": final_url,
                "score": score,
                "comments": comments,
                "category": category,
                "created_at": str(row["created_at"] or ""),
            }
        )

    lines: list[str] = []
    lines.append(f"Reddit Watchlist Digest ({window_label})")
    lines.append(f"New posts: {len(rows)}")
    lines.append(
        "Category mix: "
        f"AI={category_counts.get('ai', 0)} | "
        f"Political={category_counts.get('political', 0)} | "
        f"War={category_counts.get('war', 0)} | "
        f"Other={category_counts.get('other_interest', 0)}"
    )

    first_ts = str(rows[0]["created_at"] or "")
    last_ts = str(rows[-1]["created_at"] or "")
    lines.append(f"Window: {first_ts} -> {last_ts}")
    lines.append("")
    lines.append("Top subreddits:")
    for subreddit, count in subreddit_counts.most_common(8):
        lines.append(f"- r/{subreddit}: {count}")

    lines.append("")
    lines.append("Posts by category:")

    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        buckets.setdefault(str(item["category"]), []).append(item)

    ordered = [slug for slug in CORE_ORDER if buckets.get(slug)]
    extras = sorted(
        [slug for slug in buckets if slug not in CORE_ORDER],
        key=lambda slug: len(buckets.get(slug, [])),
        reverse=True,
    )
    ordered.extend(extras)

    emitted = 0
    max_emit = max(1, max_items)
    for slug in ordered:
        if emitted >= max_emit:
            break
        lines.append(f"{_format_category(slug)}:")
        for item in buckets.get(slug, []):
            if emitted >= max_emit:
                break
            lines.append(
                f"- r/{item['subreddit']}: {item['title']} [score={int(item['score'])}, comments={int(item['comments'])}]"
            )
            if item["url"]:
                lines.append(f"  {item['url']}")
            emitted += 1

    remaining = len(items) - emitted
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")

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
    watchlist_map: dict[str, str],
) -> tuple[str | None, dict[str, int]]:
    use_claude = os.getenv("CSI_REDDIT_DIGEST_USE_CLAUDE", "0").strip() == "1"
    if not use_claude:
        return None, {}

    api_key = _resolve_setting(["ANTHROPIC_API_KEY"], env_files)
    if not api_key:
        return None, {}

    model = os.getenv("CSI_REDDIT_DIGEST_CLAUDE_MODEL", "claude-3-5-haiku-latest").strip()
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url

    compact_posts: list[dict[str, Any]] = []
    for row in rows[: max(1, max_items * 3)]:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        subreddit = str(subject.get("subreddit") or "").strip()
        title = str(subject.get("title") or "").strip()
        compact_posts.append(
            {
                "created_at": str(row["created_at"] or ""),
                "subreddit": subreddit,
                "title": title,
                "url": str(subject.get("permalink") or subject.get("url") or "").strip(),
                "score": int(subject.get("score") or 0),
                "num_comments": int(subject.get("num_comments") or 0),
                "category": _infer_category(subreddit=subreddit, title=title, watchlist_map=watchlist_map),
            }
        )

    prompt = (
        "Create a concise Telegram digest for newly detected Reddit watchlist posts.\n"
        f"Time bucket: {window_label}\n"
        f"Total posts: {len(rows)}\n"
        f"Include at most {max_items} post bullets.\n"
        "Rules:\n"
        "- Plain text only (no markdown)\n"
        "- Keep under 3000 characters\n"
        "- Include sections: AI, Political, War, Other Interest\n"
        "- Focus on subreddit + title + URL + score/comments\n\n"
        f"Posts JSON:\n{json.dumps(compact_posts, ensure_ascii=False)}"
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
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
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
    parser = argparse.ArgumentParser(description="Send batched Reddit watchlist digest to Telegram.")
    parser.add_argument("--db-path", required=True, help="Path to CSI sqlite db")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/reddit_telegram_digest_state.json",
        help="Path to persisted digest cursor state",
    )
    parser.add_argument("--source", default="reddit_discovery", help="Event source to aggregate")
    parser.add_argument("--max-items", type=int, default=12, help="Max post bullets in one digest")
    parser.add_argument("--window-label", default="10m", help="Label for digest window")
    parser.add_argument("--chat-id", default="", help="Telegram chat id override")
    parser.add_argument("--bot-token", default="", help="Telegram bot token override")
    parser.add_argument(
        "--watchlist-file",
        default="",
        help="Path to reddit watchlist for category hints",
    )
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
        print(f"REDDIT_TELEGRAM_DB_MISSING path={db_path}")
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
        print(f"REDDIT_TELEGRAM_SEEDED_LAST_SENT_ID={max_id}")
        return 0

    last_sent_id = int(state.get("last_sent_id") or 0)
    rows = conn.execute(
        """
        SELECT id, event_id, created_at, delivered, subject_json
        FROM events
        WHERE source = ? AND delivered = 1 AND id > ?
        ORDER BY id ASC
        """,
        (source, last_sent_id),
    ).fetchall()

    print(f"REDDIT_TELEGRAM_LAST_SENT_ID={last_sent_id}")
    print(f"REDDIT_TELEGRAM_NEW_COUNT={len(rows)}")
    if not rows:
        conn.close()
        return 0

    watchlist_file = (
        (args.watchlist_file or "").strip()
        or _resolve_setting(["CSI_REDDIT_WATCHLIST_FILE"], env_files)
        or "/opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json"
    )
    watchlist_map = _load_watchlist_category_map(Path(watchlist_file).expanduser())

    chat_id = (args.chat_id or "").strip() or _resolve_chat_id(env_files)
    bot_token = (args.bot_token or "").strip() or _resolve_setting(
        ["CSI_REDDIT_TELEGRAM_BOT_TOKEN", "CSI_RSS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
        env_files,
    )

    if not chat_id:
        conn.close()
        print("REDDIT_TELEGRAM_SKIPPED_CHAT_ID_MISSING (set CSI_REDDIT_TELEGRAM_CHAT_ID)")
        return 0
    if not bot_token:
        conn.close()
        print("REDDIT_TELEGRAM_SKIPPED_BOT_TOKEN_MISSING (set CSI_REDDIT_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN)")
        return 0

    digest, usage = _maybe_build_claude_digest(
        rows,
        max_items=max(1, int(args.max_items)),
        window_label=args.window_label,
        env_files=env_files,
        watchlist_map=watchlist_map,
    )
    if digest is None:
        digest = _build_fallback_digest(
            rows,
            max_items=max(1, int(args.max_items)),
            window_label=args.window_label,
            watchlist_map=watchlist_map,
        )
    elif usage:
        token_usage_store.insert_usage(
            conn,
            process_name="reddit_digest_claude",
            model_name=os.getenv("CSI_REDDIT_DIGEST_CLAUDE_MODEL", "claude-3-5-haiku-latest").strip(),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            metadata={
                "source": source,
                "window_label": args.window_label,
                "event_count": len(rows),
            },
        )

    next_last_sent_id = int(rows[-1]["id"])
    if args.dry_run:
        conn.close()
        print("REDDIT_TELEGRAM_DRY_RUN=1")
        print("---- DIGEST BEGIN ----")
        print(digest)
        print("---- DIGEST END ----")
        print(f"REDDIT_TELEGRAM_NEXT_LAST_SENT_ID={next_last_sent_id}")
        return 0

    ok, reason = _send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=digest)
    conn.close()
    if not ok:
        print(f"REDDIT_TELEGRAM_SEND_FAILED reason={reason}")
        return 4

    _save_state(state_path, {"last_sent_id": next_last_sent_id})
    print("REDDIT_TELEGRAM_SENT=1")
    print(f"REDDIT_TELEGRAM_NEXT_LAST_SENT_ID={next_last_sent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
