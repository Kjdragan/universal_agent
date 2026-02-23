#!/usr/bin/env python3
"""Send playlist-triggered tutorial run updates to Telegram, including artifact paths."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


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
            "CSI_TUTORIAL_TELEGRAM_CHAT_ID",
            "CSI_TELEGRAM_CHAT_ID_TUTORIALS",
            "TELEGRAM_CHAT_ID_TUTORIALS",
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


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _find_tutorial_artifacts(
    *,
    artifacts_root: Path,
    video_id: str,
    max_hits: int = 3,
    max_day_dirs: int = 45,
) -> list[dict[str, str]]:
    if not video_id:
        return []
    root = artifacts_root / "youtube-tutorial-learning"
    if not root.exists() or not root.is_dir():
        return []

    day_dirs = [d for d in root.iterdir() if d.is_dir()]
    day_dirs.sort(key=lambda p: p.name, reverse=True)

    hits: list[dict[str, str]] = []
    for day_dir in day_dirs[:max(1, max_day_dirs)]:
        run_dirs = [d for d in day_dir.iterdir() if d.is_dir()]
        run_dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
        for run_dir in run_dirs:
            manifest_path = run_dir / "manifest.json"
            manifest = _safe_json(manifest_path)
            manifest_video_id = str(manifest.get("video_id") or "").strip()
            name_hit = video_id in run_dir.name
            manifest_hit = manifest_video_id == video_id
            if not (name_hit or manifest_hit):
                continue

            status = str(manifest.get("status") or "").strip() or "unknown"
            title = str(manifest.get("title") or "").strip()
            hits.append(
                {
                    "run_path": str(run_dir),
                    "manifest_path": str(manifest_path) if manifest_path.exists() else "",
                    "status": status,
                    "title": title,
                }
            )
            if len(hits) >= max_hits:
                return hits
    return hits


def _build_digest(
    rows: list[sqlite3.Row],
    *,
    max_items: int,
    artifacts_root: Path,
    window_label: str,
) -> str:
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        video_id = str(subject.get("video_id") or "").strip()
        video_url = str(subject.get("url") or "").strip()
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"

        artifacts = _find_tutorial_artifacts(
            artifacts_root=artifacts_root,
            video_id=video_id,
        )
        items.append(
            {
                "video_id": video_id,
                "title": str(subject.get("title") or "(untitled)").strip() or "(untitled)",
                "video_url": video_url,
                "playlist_id": str(subject.get("playlist_id") or "").strip(),
                "created_at": str(row["created_at"] or ""),
                "artifacts": artifacts,
            }
        )

    first_ts = str(rows[0]["created_at"] or "")
    last_ts = str(rows[-1]["created_at"] or "")

    lines: list[str] = []
    lines.append(f"Playlist Tutorial Digest ({window_label})")
    lines.append(f"New playlist videos: {len(rows)}")
    lines.append(f"Window: {first_ts} -> {last_ts}")
    lines.append("")
    lines.append("Videos:")

    emit = 0
    for item in items:
        if emit >= max(1, max_items):
            break
        lines.append(f"- {item['title']}")
        lines.append(f"  video_id={item['video_id']} playlist_id={item['playlist_id']}")
        if item["video_url"]:
            lines.append(f"  {item['video_url']}")

        artifacts = item.get("artifacts") or []
        if artifacts:
            lines.append("  tutorial_artifacts:")
            for artifact in artifacts[:2]:
                run_path = str(artifact.get("run_path") or "")
                status = str(artifact.get("status") or "unknown")
                manifest_path = str(artifact.get("manifest_path") or "")
                lines.append(f"  - status={status} run={run_path}")
                if manifest_path:
                    lines.append(f"    manifest={manifest_path}")
        else:
            lines.append("  tutorial_artifacts: pending (no run artifact found yet)")

        emit += 1

    remaining = len(items) - emit
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")

    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3897] + "..."
    return msg


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
    parser = argparse.ArgumentParser(description="Send playlist tutorial updates to Telegram.")
    parser.add_argument("--db-path", required=True, help="Path to CSI sqlite db")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/playlist_tutorial_digest_state.json",
        help="Path to persisted digest cursor state",
    )
    parser.add_argument("--source", default="youtube_playlist", help="Event source to aggregate")
    parser.add_argument("--max-items", type=int, default=8, help="Max video bullets in one digest")
    parser.add_argument("--window-label", default="10m", help="Label for digest window")
    parser.add_argument("--chat-id", default="", help="Telegram chat id override")
    parser.add_argument("--bot-token", default="", help="Telegram bot token override")
    parser.add_argument(
        "--artifacts-root",
        default="",
        help="Artifacts root override (falls back to UA_ARTIFACTS_DIR or /opt/universal_agent/artifacts)",
    )
    parser.add_argument(
        "--env-file",
        default="/opt/universal_agent/.env",
        help="Fallback env file for Telegram keys",
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
        print(f"PLAYLIST_TUTORIAL_DB_MISSING path={db_path}")
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
        print(f"PLAYLIST_TUTORIAL_SEEDED_LAST_SENT_ID={max_id}")
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
    conn.close()

    print(f"PLAYLIST_TUTORIAL_LAST_SENT_ID={last_sent_id}")
    print(f"PLAYLIST_TUTORIAL_NEW_COUNT={len(rows)}")
    if not rows:
        return 0

    chat_id = (args.chat_id or "").strip() or _resolve_chat_id(env_files)
    bot_token = (args.bot_token or "").strip() or _resolve_setting(
        ["CSI_TUTORIAL_TELEGRAM_BOT_TOKEN", "CSI_RSS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
        env_files,
    )

    if not chat_id:
        print("PLAYLIST_TUTORIAL_SKIPPED_CHAT_ID_MISSING (set CSI_TUTORIAL_TELEGRAM_CHAT_ID)")
        return 0
    if not bot_token:
        print("PLAYLIST_TUTORIAL_SKIPPED_BOT_TOKEN_MISSING (set CSI_TUTORIAL_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN)")
        return 0

    artifacts_root_raw = (args.artifacts_root or "").strip() or _resolve_setting(
        ["CSI_TUTORIAL_ARTIFACTS_ROOT", "UA_ARTIFACTS_DIR"],
        env_files,
    )
    artifacts_root = Path(artifacts_root_raw).expanduser() if artifacts_root_raw else Path("/opt/universal_agent/artifacts")

    digest = _build_digest(
        rows,
        max_items=max(1, int(args.max_items)),
        artifacts_root=artifacts_root,
        window_label=args.window_label,
    )

    next_last_sent_id = int(rows[-1]["id"])
    if args.dry_run:
        print("PLAYLIST_TUTORIAL_DRY_RUN=1")
        print("---- DIGEST BEGIN ----")
        print(digest)
        print("---- DIGEST END ----")
        print(f"PLAYLIST_TUTORIAL_NEXT_LAST_SENT_ID={next_last_sent_id}")
        return 0

    ok, reason = _send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=digest)
    if not ok:
        print(f"PLAYLIST_TUTORIAL_SEND_FAILED reason={reason}")
        return 4

    _save_state(state_path, {"last_sent_id": next_last_sent_id})
    print("PLAYLIST_TUTORIAL_SENT=1")
    print(f"PLAYLIST_TUTORIAL_NEXT_LAST_SENT_ID={next_last_sent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
