#!/usr/bin/env python3
"""Send playlist-triggered tutorial run updates to Telegram, including artifact paths."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from datetime import datetime, timezone


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


def _resolve_int_arg_or_env(
    *,
    cli_value: int | None,
    keys: list[str],
    env_files: list[Path],
    default: int,
    minimum: int = 0,
) -> int:
    if cli_value is not None:
        return max(minimum, int(cli_value))
    raw = _resolve_setting(keys, env_files)
    if raw:
        try:
            return max(minimum, int(raw.strip()))
        except Exception:
            pass
    return max(minimum, int(default))


def _is_truthy(raw: str) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_thread_id(raw: str) -> int | None:
    val = (raw or "").strip()
    if not val:
        return None
    try:
        parsed = int(val)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed


def _resolve_chat_id(env_files: list[Path], *, strict: bool = False) -> str:
    keys = [
        "CSI_TUTORIAL_TELEGRAM_CHAT_ID",
        "CSI_TELEGRAM_CHAT_ID_TUTORIALS",
        "TELEGRAM_CHAT_ID_TUTORIALS",
    ]
    if not strict:
        keys.extend(
            [
                "CSI_RSS_TELEGRAM_CHAT_ID",
                "TELEGRAM_CHAT_ID",
                "TELEGRAM_DEFAULT_CHAT_ID",
            ]
        )

    chat_id = _resolve_setting(keys, env_files)
    if chat_id:
        return chat_id

    if strict:
        return ""

    raw_allowed = _resolve_setting(["TELEGRAM_ALLOWED_USER_IDS"], env_files)
    if raw_allowed:
        first = raw_allowed.split(",", 1)[0].strip()
        if first:
            return first
    return ""


def _resolve_thread_id(env_files: list[Path], *, strict: bool = False) -> int | None:
    keys = [
        "CSI_TUTORIAL_TELEGRAM_THREAD_ID",
        "CSI_TELEGRAM_THREAD_ID_TUTORIALS",
        "TELEGRAM_THREAD_ID_TUTORIALS",
    ]
    if not strict:
        keys.extend(["CSI_RSS_TELEGRAM_THREAD_ID", "TELEGRAM_THREAD_ID"])
    return _parse_optional_thread_id(_resolve_setting(keys, env_files))


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _relative_path_under_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return ""


def _artifact_file_url(base_url: str, rel_path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    rel = str(rel_path or "").strip().strip("/")
    if not base or not rel:
        return ""
    return f"{base}/api/artifacts/files/{urllib.parse.quote(rel, safe='/')}"


def _artifact_viewer_url(dashboard_base_url: str, rel_path: str) -> str:
    base = (dashboard_base_url or "").strip().rstrip("/")
    rel = str(rel_path or "").strip().strip("/")
    if not base or not rel:
        return ""
    query = urllib.parse.urlencode(
        {
            "tab": "explorer",
            "scope": "artifacts",
            "path": rel,
            "preview": rel,
        }
    )
    return f"{base}/storage?{query}"


def _artifact_run_viewer_url(dashboard_base_url: str, run_rel_path: str) -> str:
    base = (dashboard_base_url or "").strip().rstrip("/")
    rel = str(run_rel_path or "").strip().strip("/")
    if not base or not rel:
        return ""
    query = urllib.parse.urlencode(
        {
            "tab": "explorer",
            "scope": "artifacts",
            "path": rel,
        }
    )
    return f"{base}/storage?{query}"


def _collect_high_value_files(
    *,
    run_dir: Path,
    artifacts_root: Path,
    artifacts_base_url: str,
    dashboard_base_url: str = "",
    max_code_files: int = 3,
) -> list[dict[str, str]]:
    preferred: list[tuple[str, Path]] = []
    for label, rel in (
        ("README", "README.md"),
        ("Concept", "CONCEPT.md"),
        ("Implementation Guide", "IMPLEMENTATION.md"),
    ):
        candidate = run_dir / rel
        if candidate.exists() and candidate.is_file():
            preferred.append((label, candidate))

    impl_dir = run_dir / "implementation"
    if impl_dir.exists() and impl_dir.is_dir():
        code_candidates: list[Path] = []
        for ext in ("*.py", "*.ts", "*.tsx", "*.js", "*.sh", "*.ipynb"):
            code_candidates.extend(impl_dir.glob(ext))
        code_candidates = [p for p in code_candidates if p.is_file()]
        code_candidates.sort(key=lambda p: p.name.lower())
        for path in code_candidates[: max(1, max_code_files)]:
            preferred.append((f"Code: {path.name}", path))

    out: list[dict[str, str]] = []
    for label, file_path in preferred:
        rel = _relative_path_under_root(file_path, artifacts_root)
        out.append(
            {
                "label": label,
                "path": str(file_path),
                "rel_path": rel,
                "url": _artifact_file_url(artifacts_base_url, rel),
                "viewer_url": _artifact_viewer_url(dashboard_base_url, rel),
            }
        )
    return out


def _find_tutorial_artifacts(
    *,
    artifacts_root: Path,
    video_id: str,
    max_hits: int = 3,
    max_day_dirs: int = 45,
    artifacts_base_url: str = "",
    dashboard_base_url: str = "",
) -> list[dict[str, Any]]:
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
            run_url = ""
            run_viewer_url = ""
            manifest_url = ""
            run_rel = _relative_path_under_root(run_dir, artifacts_root)
            base = (artifacts_base_url or "").strip().rstrip("/")
            run_viewer_url = _artifact_run_viewer_url(dashboard_base_url, run_rel)
            if base:
                try:
                    rel_run = run_rel or run_dir.resolve().relative_to(artifacts_root.resolve()).as_posix()
                    run_url = f"{base}/api/artifacts?path={urllib.parse.quote(rel_run, safe='/')}"
                except Exception:
                    run_url = ""
                if manifest_path.exists():
                    try:
                        rel_manifest = manifest_path.resolve().relative_to(artifacts_root.resolve()).as_posix()
                        manifest_url = f"{base}/api/artifacts/files/{urllib.parse.quote(rel_manifest, safe='/')}"
                    except Exception:
                        manifest_url = ""
            high_value_files = _collect_high_value_files(
                run_dir=run_dir,
                artifacts_root=artifacts_root,
                artifacts_base_url=artifacts_base_url,
                dashboard_base_url=dashboard_base_url,
            )
            hits.append(
                {
                    "run_path": str(run_dir),
                    "run_rel_path": run_rel,
                    "manifest_path": str(manifest_path) if manifest_path.exists() else "",
                    "status": status,
                    "title": title,
                    "run_url": run_url,
                    "run_viewer_url": run_viewer_url,
                    "manifest_url": manifest_url,
                    "high_value_files": high_value_files,
                }
            )
            if len(hits) >= max_hits:
                return hits
    return hits


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime_any(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _minutes_since(value: str, *, now: datetime | None = None) -> int | None:
    parsed = _parse_datetime_any(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    delta = current - parsed
    return max(0, int(delta.total_seconds() // 60))


def _find_workspace_session_hints(
    *,
    workspace_root: Path,
    video_id: str,
    max_hits: int = 3,
) -> list[str]:
    if not video_id:
        return []
    if not workspace_root.exists() or not workspace_root.is_dir():
        return []
    pattern = f"session_hook_yt_*_{video_id}"
    hits: list[tuple[float, Path]] = []
    for candidate in workspace_root.glob(pattern):
        if not candidate.is_dir():
            continue
        try:
            mtime = float(candidate.stat().st_mtime)
        except Exception:
            mtime = 0.0
        hits.append((mtime, candidate))
    hits.sort(key=lambda item: item[0], reverse=True)
    out: list[str] = []
    for _, path in hits[: max(1, max_hits)]:
        out.append(str(path))
    return out


def _find_stalled_workspace_turns(
    *,
    workspace_root: Path,
    video_id: str,
    min_age_minutes: int,
    now: datetime,
    max_hits: int = 3,
) -> list[dict[str, Any]]:
    if not video_id:
        return []
    if not workspace_root.exists() or not workspace_root.is_dir():
        return []
    pattern = f"session_hook_yt_*_{video_id}"
    sessions: list[tuple[float, Path]] = []
    for candidate in workspace_root.glob(pattern):
        if not candidate.is_dir():
            continue
        try:
            mtime = float(candidate.stat().st_mtime)
        except Exception:
            mtime = 0.0
        sessions.append((mtime, candidate))
    sessions.sort(key=lambda item: item[0], reverse=True)

    stalled: list[dict[str, Any]] = []
    for _, session_dir in sessions:
        turns_dir = session_dir / "turns"
        if not turns_dir.exists() or not turns_dir.is_dir():
            continue
        turn_files = sorted(
            turns_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        for turn_file in turn_files:
            started_at = ""
            finalized = False
            try:
                for line in turn_file.read_text(encoding="utf-8").splitlines():
                    raw = line.strip()
                    if not raw:
                        continue
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        continue
                    kind = str(event.get("event") or "").strip()
                    if kind == "turn_started" and not started_at:
                        started_at = str(event.get("timestamp") or "").strip()
                    elif kind == "turn_finalized":
                        finalized = True
                if finalized or not started_at:
                    continue
            except Exception:
                continue

            age_minutes = _minutes_since(started_at, now=now)
            if age_minutes is None or age_minutes < min_age_minutes:
                continue

            stalled.append(
                {
                    "session_path": str(session_dir),
                    "turn_id": turn_file.stem,
                    "started_at": started_at,
                    "age_minutes": age_minutes,
                }
            )
            break
        if len(stalled) >= max(1, max_hits):
            return stalled
    return stalled


def _item_from_row(
    row: sqlite3.Row,
    *,
    artifacts_root: Path,
    artifacts_base_url: str = "",
    dashboard_base_url: str = "",
) -> dict[str, Any]:
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
        artifacts_base_url=artifacts_base_url,
        dashboard_base_url=dashboard_base_url,
    )
    return {
        "video_id": video_id,
        "title": str(subject.get("title") or "(untitled)").strip() or "(untitled)",
        "video_url": video_url,
        "playlist_id": str(subject.get("playlist_id") or "").strip(),
        "created_at": str(row["created_at"] or ""),
        "artifacts": artifacts,
    }


def _build_digest_from_items(
    items: list[dict[str, Any]],
    *,
    first_ts: str,
    last_ts: str,
    max_items: int,
    window_label: str,
    dashboard_base_url: str = "",
) -> str:
    lines: list[str] = []
    lines.append(f"Playlist Tutorial Digest ({window_label})")
    lines.append(f"New playlist videos: {len(items)}")
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
            lines.append("  Result package found:")
            for artifact in artifacts[:2]:
                status = str(artifact.get("status") or "unknown")
                run_url = str(artifact.get("run_url") or "")
                run_viewer_url = str(artifact.get("run_viewer_url") or "")
                lines.append(f"  - status={status}")
                if run_viewer_url or run_url:
                    lines.append(f"    view_results={run_viewer_url or run_url}")
                for hv in (artifact.get("high_value_files") or [])[:3]:
                    label = str(hv.get("label") or "file")
                    url = str(hv.get("url") or "")
                    viewer_url = str(hv.get("viewer_url") or "")
                    if viewer_url or url:
                        lines.append(f"    {label}: {viewer_url or url}")
        else:
            lines.append("  tutorial_artifacts: pending (no run artifact found yet)")

        emit += 1

    remaining = len(items) - emit
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    dash = (dashboard_base_url or "").strip().rstrip("/")
    if dash:
        lines.append("")
        lines.append(f"Dashboard backlog: {dash}/dashboard/tutorials")

    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3897] + "..."
    return msg


def _build_followup_digest(
    ready_items: list[dict[str, Any]],
    *,
    max_items: int,
    window_label: str,
    dashboard_base_url: str = "",
) -> str:
    lines: list[str] = []
    lines.append(f"Tutorial Artifacts Ready ({window_label})")
    lines.append(f"Ready items: {len(ready_items)}")
    lines.append("")
    lines.append("Ready to review:")

    emit = 0
    for item in ready_items:
        if emit >= max(1, max_items):
            break
        lines.append(f"- {item.get('title') or '(untitled)'}")
        lines.append(f"  video_id={item.get('video_id') or ''}")
        if item.get("video_url"):
            lines.append(f"  watch={item['video_url']}")
        artifacts = item.get("artifacts") or []
        for artifact in artifacts[:2]:
            status = str(artifact.get("status") or "unknown")
            run_url = str(artifact.get("run_url") or "")
            run_viewer_url = str(artifact.get("run_viewer_url") or "")
            lines.append(f"  - package_status={status}")
            if run_viewer_url or run_url:
                lines.append(f"    view_results={run_viewer_url or run_url}")
            for hv in (artifact.get("high_value_files") or [])[:5]:
                label = str(hv.get("label") or "file")
                url = str(hv.get("url") or "")
                viewer_url = str(hv.get("viewer_url") or "")
                if viewer_url or url:
                    lines.append(f"    {label}: {viewer_url or url}")
        emit += 1

    remaining = len(ready_items) - emit
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    dash = (dashboard_base_url or "").strip().rstrip("/")
    if dash:
        lines.append("")
        lines.append(f"Queue for Simone: {dash}/dashboard/tutorials")

    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3897] + "..."
    return msg


def _build_pending_digest(
    pending_items: list[dict[str, Any]],
    *,
    max_items: int,
    window_label: str,
) -> str:
    lines: list[str] = []
    lines.append(f"Tutorial Artifacts Pending ({window_label})")
    lines.append(f"Pending items: {len(pending_items)}")
    lines.append("")
    lines.append("Waiting on artifacts for:")

    emit = 0
    for item in pending_items:
        if emit >= max(1, max_items):
            break
        lines.append(f"- {item.get('title') or '(untitled)'}")
        lines.append(f"  video_id={item.get('video_id') or ''} playlist_id={item.get('playlist_id') or ''}")
        if item.get("video_url"):
            lines.append(f"  {item['video_url']}")
        age_minutes = item.get("age_minutes")
        if isinstance(age_minutes, int):
            lines.append(f"  pending_age_minutes={age_minutes}")
        last_checked = str(item.get("last_checked_at") or "")
        if last_checked:
            lines.append(f"  last_checked_at={last_checked}")
        for hint in (item.get("workspace_hints") or [])[:2]:
            lines.append(f"  workspace_hint={hint}")
        lines.append("  status_hint=waiting_for_tutorial_artifacts")
        emit += 1

    remaining = len(pending_items) - emit
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")

    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3897] + "..."
    return msg


def _build_stalled_digest(
    stalled_items: list[dict[str, Any]],
    *,
    max_items: int,
    window_label: str,
) -> str:
    lines: list[str] = []
    lines.append(f"Tutorial Session Stalled ({window_label})")
    lines.append(f"Potentially stuck items: {len(stalled_items)}")
    lines.append("")
    lines.append("Detected open turns without turn_finalized:")

    emit = 0
    for item in stalled_items:
        if emit >= max(1, max_items):
            break
        lines.append(f"- {item.get('title') or '(untitled)'}")
        lines.append(f"  video_id={item.get('video_id') or ''} playlist_id={item.get('playlist_id') or ''}")
        if item.get("video_url"):
            lines.append(f"  {item['video_url']}")
        for turn in (item.get("stalled_turns") or [])[:2]:
            lines.append(
                "  stalled_turn="
                f"{turn.get('turn_id') or ''} age_minutes={turn.get('age_minutes') or 0} started_at={turn.get('started_at') or ''}"
            )
            lines.append(f"  workspace_hint={turn.get('session_path') or ''}")
        lines.append("  status_hint=turn_started_without_turn_finalized")
        emit += 1

    remaining = len(stalled_items) - emit
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")

    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3897] + "..."
    return msg


def _send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    thread_id: int | None = None,
) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id is not None:
        body["message_thread_id"] = thread_id
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
    parser.add_argument("--thread-id", default="", help="Telegram topic/thread id override")
    parser.add_argument(
        "--artifacts-root",
        default="",
        help="Artifacts root override (falls back to UA_ARTIFACTS_DIR or /opt/universal_agent/artifacts)",
    )
    parser.add_argument(
        "--artifacts-base-url",
        default="",
        help="Optional public UA API base URL for clickable artifact links (example: https://api.example.com).",
    )
    parser.add_argument(
        "--dashboard-base-url",
        default="",
        help="Optional public App/Dashboard base URL for tutorial queue links (example: https://app.example.com).",
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
    parser.add_argument(
        "--strict-stream-routing",
        action="store_true",
        help="Require stream-specific tutorial Telegram chat/thread ids and disable fallback to RSS/default chat ids.",
    )
    parser.add_argument(
        "--backfill-pending-count",
        type=int,
        default=20,
        help="Backfill unresolved pending videos from recent delivered playlist events up to this count.",
    )
    parser.add_argument(
        "--pending-reminder-minutes",
        type=int,
        default=None,
        help="When pending artifacts are older than this many minutes, send a pending reminder digest.",
    )
    parser.add_argument(
        "--pending-reminder-cooldown-minutes",
        type=int,
        default=None,
        help="Minimum minutes between pending reminder messages for the same video.",
    )
    parser.add_argument(
        "--stalled-turn-minutes",
        type=int,
        default=None,
        help="Alert when open tutorial turns exceed this age in minutes without turn_finalized.",
    )
    parser.add_argument(
        "--stalled-turn-cooldown-minutes",
        type=int,
        default=None,
        help="Minimum minutes between stalled-turn alerts for the same video.",
    )
    parser.add_argument(
        "--stalled-turn-max-items",
        type=int,
        default=6,
        help="Maximum stalled-turn bullets in one Telegram message.",
    )
    parser.add_argument(
        "--workspace-root",
        default="",
        help="Workspace root for session hint discovery (defaults to /opt/universal_agent/AGENT_RUN_WORKSPACES).",
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
    pending_by_video_raw = state.get("pending_by_video") if isinstance(state, dict) else {}
    pending_by_video: dict[str, dict[str, Any]] = {}
    if isinstance(pending_by_video_raw, dict):
        for k, v in pending_by_video_raw.items():
            if not isinstance(v, dict):
                continue
            key = str(k or "").strip()
            if key:
                pending_by_video[key] = dict(v)

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

    print(f"PLAYLIST_TUTORIAL_LAST_SENT_ID={last_sent_id}")
    print(f"PLAYLIST_TUTORIAL_NEW_COUNT={len(rows)}")
    print(f"PLAYLIST_TUTORIAL_PENDING_TRACKED={len(pending_by_video)}")

    strict_stream_routing = args.strict_stream_routing or _is_truthy(
        _resolve_setting(
            ["CSI_TUTORIAL_TELEGRAM_STRICT_STREAM_ROUTING", "CSI_TELEGRAM_STRICT_STREAM_ROUTING"],
            env_files,
        )
    )

    chat_id = (args.chat_id or "").strip() or _resolve_chat_id(env_files, strict=strict_stream_routing)
    thread_id = _parse_optional_thread_id(args.thread_id) or _resolve_thread_id(
        env_files,
        strict=strict_stream_routing,
    )
    bot_token = (args.bot_token or "").strip() or _resolve_setting(
        ["CSI_TUTORIAL_TELEGRAM_BOT_TOKEN", "CSI_RSS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
        env_files,
    )

    if not chat_id:
        if strict_stream_routing:
            print(
                "PLAYLIST_TUTORIAL_SKIPPED_CHAT_ID_MISSING_STRICT "
                "(set CSI_TUTORIAL_TELEGRAM_CHAT_ID or disable strict stream routing)"
            )
        else:
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
    artifacts_base_url = (args.artifacts_base_url or "").strip() or _resolve_setting(
        ["CSI_TUTORIAL_ARTIFACTS_BASE_URL", "CSI_ARTIFACTS_BASE_URL", "UA_API_BASE_URL"],
        env_files,
    )
    dashboard_base_url = (args.dashboard_base_url or "").strip() or _resolve_setting(
        ["CSI_TUTORIAL_DASHBOARD_BASE_URL", "CSI_DASHBOARD_BASE_URL", "UA_DASHBOARD_BASE_URL"],
        env_files,
    )
    if not dashboard_base_url and artifacts_base_url:
        api_hint = "://api."
        if api_hint in artifacts_base_url:
            dashboard_base_url = artifacts_base_url.replace(api_hint, "://app.", 1)
    workspace_root_raw = (args.workspace_root or "").strip() or _resolve_setting(
        ["CSI_TUTORIAL_WORKSPACE_ROOT", "UA_WORKSPACES_DIR"],
        env_files,
    )
    workspace_root = (
        Path(workspace_root_raw).expanduser()
        if workspace_root_raw
        else Path("/opt/universal_agent/AGENT_RUN_WORKSPACES")
    )

    items = [
        _item_from_row(
            row,
            artifacts_root=artifacts_root,
            artifacts_base_url=artifacts_base_url,
            dashboard_base_url=dashboard_base_url,
        )
        for row in rows
    ]
    next_last_sent_id = last_sent_id
    next_pending_by_video = dict(pending_by_video)

    if rows:
        first_ts = str(rows[0]["created_at"] or "")
        last_ts = str(rows[-1]["created_at"] or "")
        digest = _build_digest_from_items(
            items,
            first_ts=first_ts,
            last_ts=last_ts,
            max_items=max(1, int(args.max_items)),
            window_label=args.window_label,
            dashboard_base_url=dashboard_base_url,
        )
        next_last_sent_id = int(rows[-1]["id"])
        if args.dry_run:
            print("PLAYLIST_TUTORIAL_DRY_RUN=1")
            print("---- DIGEST BEGIN ----")
            print(digest)
            print("---- DIGEST END ----")
            print(f"PLAYLIST_TUTORIAL_NEXT_LAST_SENT_ID={next_last_sent_id}")
        else:
            ok, reason = _send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=digest,
                thread_id=thread_id,
            )
            if not ok:
                print(f"PLAYLIST_TUTORIAL_SEND_FAILED reason={reason}")
                return 4
            print("PLAYLIST_TUTORIAL_SENT=1")
            print(f"PLAYLIST_TUTORIAL_NEXT_LAST_SENT_ID={next_last_sent_id}")

        for item in items:
            video_id = str(item.get("video_id") or "").strip()
            if not video_id:
                continue
            if item.get("artifacts"):
                next_pending_by_video.pop(video_id, None)
                continue
            next_pending_by_video[video_id] = {
                "video_id": video_id,
                "title": str(item.get("title") or ""),
                "video_url": str(item.get("video_url") or ""),
                "playlist_id": str(item.get("playlist_id") or ""),
                "created_at": str(item.get("created_at") or ""),
                "pending_since": _now_iso_utc(),
            }

    backfill_count = max(0, int(args.backfill_pending_count))
    backfilled = 0
    if backfill_count > 0 and last_sent_id > 0:
        backfill_rows = conn.execute(
            """
            SELECT id, created_at, subject_json
            FROM events
            WHERE source = ? AND delivered = 1 AND id <= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (source, last_sent_id, backfill_count),
        ).fetchall()
        for row in backfill_rows:
            item = _item_from_row(
                row,
                artifacts_root=artifacts_root,
                artifacts_base_url=artifacts_base_url,
                dashboard_base_url=dashboard_base_url,
            )
            video_id = str(item.get("video_id") or "").strip()
            if not video_id:
                continue
            if video_id in next_pending_by_video:
                continue
            if item.get("artifacts"):
                continue
            next_pending_by_video[video_id] = {
                "video_id": video_id,
                "title": str(item.get("title") or ""),
                "video_url": str(item.get("video_url") or ""),
                "playlist_id": str(item.get("playlist_id") or ""),
                "created_at": str(item.get("created_at") or ""),
                "pending_since": _now_iso_utc(),
            }
            backfilled += 1
    conn.close()
    print(f"PLAYLIST_TUTORIAL_PENDING_BACKFILLED={backfilled}")

    ready_items: list[dict[str, Any]] = []
    remaining_pending: dict[str, dict[str, Any]] = {}
    for video_id, meta in next_pending_by_video.items():
        artifacts = _find_tutorial_artifacts(
            artifacts_root=artifacts_root,
            video_id=video_id,
            artifacts_base_url=artifacts_base_url,
            dashboard_base_url=dashboard_base_url,
        )
        if artifacts:
            ready_items.append(
                {
                    "video_id": video_id,
                    "title": str(meta.get("title") or ""),
                    "video_url": str(meta.get("video_url") or ""),
                    "playlist_id": str(meta.get("playlist_id") or ""),
                    "created_at": str(meta.get("created_at") or ""),
                    "artifacts": artifacts,
                }
            )
        else:
            updated = dict(meta)
            updated["last_checked_at"] = _now_iso_utc()
            remaining_pending[video_id] = updated

    print(f"PLAYLIST_TUTORIAL_FOLLOWUP_READY_COUNT={len(ready_items)}")

    pending_for_state: dict[str, dict[str, Any]]
    if ready_items:
        followup = _build_followup_digest(
            ready_items,
            max_items=max(1, int(args.max_items)),
            window_label=args.window_label,
            dashboard_base_url=dashboard_base_url,
        )
        if args.dry_run:
            print("PLAYLIST_TUTORIAL_FOLLOWUP_DRY_RUN=1")
            print("---- FOLLOWUP DIGEST BEGIN ----")
            print(followup)
            print("---- FOLLOWUP DIGEST END ----")
            pending_for_state = remaining_pending
        else:
            ok, reason = _send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=followup,
                thread_id=thread_id,
            )
            if ok:
                print("PLAYLIST_TUTORIAL_FOLLOWUP_SENT=1")
                pending_for_state = remaining_pending
            else:
                print(f"PLAYLIST_TUTORIAL_FOLLOWUP_SEND_FAILED reason={reason}")
                pending_for_state = next_pending_by_video
    else:
        pending_for_state = remaining_pending

    reminder_threshold_minutes = _resolve_int_arg_or_env(
        cli_value=args.pending_reminder_minutes,
        keys=["CSI_TUTORIAL_PENDING_REMINDER_MINUTES"],
        env_files=env_files,
        default=30,
        minimum=0,
    )
    reminder_cooldown_minutes = _resolve_int_arg_or_env(
        cli_value=args.pending_reminder_cooldown_minutes,
        keys=["CSI_TUTORIAL_PENDING_REMINDER_COOLDOWN_MINUTES"],
        env_files=env_files,
        default=120,
        minimum=1,
    )
    stalled_threshold_minutes = _resolve_int_arg_or_env(
        cli_value=args.stalled_turn_minutes,
        keys=["CSI_TUTORIAL_STALLED_TURN_MINUTES"],
        env_files=env_files,
        default=15,
        minimum=0,
    )
    stalled_cooldown_minutes = _resolve_int_arg_or_env(
        cli_value=args.stalled_turn_cooldown_minutes,
        keys=["CSI_TUTORIAL_STALLED_TURN_COOLDOWN_MINUTES"],
        env_files=env_files,
        default=90,
        minimum=1,
    )
    print(f"PLAYLIST_TUTORIAL_PENDING_REMINDER_MINUTES={reminder_threshold_minutes}")
    print(f"PLAYLIST_TUTORIAL_PENDING_REMINDER_COOLDOWN_MINUTES={reminder_cooldown_minutes}")
    print(f"PLAYLIST_TUTORIAL_STALLED_TURN_MINUTES={stalled_threshold_minutes}")
    print(f"PLAYLIST_TUTORIAL_STALLED_TURN_COOLDOWN_MINUTES={stalled_cooldown_minutes}")

    now_utc = datetime.now(timezone.utc)
    pending_reminder_items: list[dict[str, Any]] = []
    if pending_for_state and reminder_threshold_minutes > 0:
        for video_id, meta in list(pending_for_state.items()):
            age_minutes = _minutes_since(
                str(meta.get("pending_since") or meta.get("created_at") or ""),
                now=now_utc,
            )
            if age_minutes is None or age_minutes < reminder_threshold_minutes:
                continue
            since_last_reminder = _minutes_since(str(meta.get("last_pending_reminder_at") or ""), now=now_utc)
            if since_last_reminder is not None and since_last_reminder < reminder_cooldown_minutes:
                continue

            workspace_hints = _find_workspace_session_hints(
                workspace_root=workspace_root,
                video_id=video_id,
            )
            pending_reminder_items.append(
                {
                    "video_id": video_id,
                    "title": str(meta.get("title") or ""),
                    "video_url": str(meta.get("video_url") or ""),
                    "playlist_id": str(meta.get("playlist_id") or ""),
                    "last_checked_at": str(meta.get("last_checked_at") or ""),
                    "age_minutes": age_minutes,
                    "workspace_hints": workspace_hints,
                }
            )
            updated = dict(meta)
            updated["last_pending_reminder_at"] = _now_iso_utc()
            pending_for_state[video_id] = updated
    print(f"PLAYLIST_TUTORIAL_PENDING_REMINDER_COUNT={len(pending_reminder_items)}")
    if pending_reminder_items:
        pending_digest = _build_pending_digest(
            pending_reminder_items,
            max_items=max(1, int(args.max_items)),
            window_label=args.window_label,
        )
        if args.dry_run:
            print("PLAYLIST_TUTORIAL_PENDING_REMINDER_DRY_RUN=1")
            print("---- PENDING DIGEST BEGIN ----")
            print(pending_digest)
            print("---- PENDING DIGEST END ----")
        else:
            ok, reason = _send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=pending_digest,
                thread_id=thread_id,
            )
            if ok:
                print("PLAYLIST_TUTORIAL_PENDING_REMINDER_SENT=1")
            else:
                print(f"PLAYLIST_TUTORIAL_PENDING_REMINDER_SEND_FAILED reason={reason}")

    stalled_items: list[dict[str, Any]] = []
    if pending_for_state and stalled_threshold_minutes > 0:
        for video_id, meta in list(pending_for_state.items()):
            since_last_stalled_alert = _minutes_since(str(meta.get("last_stalled_alert_at") or ""), now=now_utc)
            if since_last_stalled_alert is not None and since_last_stalled_alert < stalled_cooldown_minutes:
                continue
            stalled_turns = _find_stalled_workspace_turns(
                workspace_root=workspace_root,
                video_id=video_id,
                min_age_minutes=stalled_threshold_minutes,
                now=now_utc,
                max_hits=max(1, int(args.stalled_turn_max_items)),
            )
            if not stalled_turns:
                continue
            stalled_items.append(
                {
                    "video_id": video_id,
                    "title": str(meta.get("title") or ""),
                    "video_url": str(meta.get("video_url") or ""),
                    "playlist_id": str(meta.get("playlist_id") or ""),
                    "stalled_turns": stalled_turns,
                }
            )
            updated = dict(meta)
            updated["last_stalled_alert_at"] = _now_iso_utc()
            pending_for_state[video_id] = updated
    print(f"PLAYLIST_TUTORIAL_STALLED_COUNT={len(stalled_items)}")
    if stalled_items:
        stalled_digest = _build_stalled_digest(
            stalled_items,
            max_items=max(1, int(args.stalled_turn_max_items)),
            window_label=args.window_label,
        )
        if args.dry_run:
            print("PLAYLIST_TUTORIAL_STALLED_DRY_RUN=1")
            print("---- STALLED DIGEST BEGIN ----")
            print(stalled_digest)
            print("---- STALLED DIGEST END ----")
        else:
            ok, reason = _send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=stalled_digest,
                thread_id=thread_id,
            )
            if ok:
                print("PLAYLIST_TUTORIAL_STALLED_SENT=1")
            else:
                print(f"PLAYLIST_TUTORIAL_STALLED_SEND_FAILED reason={reason}")

    _save_state(
        state_path,
        {
            "last_sent_id": next_last_sent_id,
            "pending_by_video": pending_for_state,
        },
    )
    print(f"PLAYLIST_TUTORIAL_PENDING_REMAINING={len(pending_for_state)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
