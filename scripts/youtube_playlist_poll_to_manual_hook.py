#!/usr/bin/env python3
"""
Poll one or more YouTube playlists and forward newly detected videos to the
local manual hook endpoint.

This bypasses third-party trigger providers (e.g., Composio) and uses YouTube's
public Atom feed for playlist updates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
DEFAULT_HOOK_URL = "http://127.0.0.1:8002/api/v1/hooks/youtube/manual"
DEFAULT_STATE_FILE = "/opt/universal_agent/AGENT_RUN_WORKSPACES/youtube_playlist_trigger_state.json"
DEFAULT_MODE = "explainer_only"


def _log(message: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{now}] {message}")


@dataclass
class PlaylistEntry:
    playlist_id: str
    video_id: str
    video_url: str
    title: str
    channel_id: str
    published: str
    updated: str


def _safe_text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _fetch_playlist_feed(playlist_id: str, timeout_seconds: int) -> list[PlaylistEntry]:
    url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={urllib.parse.quote(playlist_id)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ua-youtube-playlist-poller/1.0",
            "Accept": "application/atom+xml,application/xml,text/xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read()
    root = ET.fromstring(raw)

    entries: list[PlaylistEntry] = []
    for entry in root.findall("a:entry", ATOM_NS):
        video_id = _safe_text(entry.find("yt:videoId", ATOM_NS))
        if not video_id:
            # Fallback from atom id form: "yt:video:<id>"
            atom_id = _safe_text(entry.find("a:id", ATOM_NS))
            if atom_id and ":" in atom_id:
                video_id = atom_id.rsplit(":", 1)[-1]
        if not video_id:
            continue

        link_node = entry.find("a:link", ATOM_NS)
        href = (link_node.attrib.get("href", "").strip() if link_node is not None else "")
        video_url = href or f"https://www.youtube.com/watch?v={video_id}"

        title = _safe_text(entry.find("a:title", ATOM_NS))
        published = _safe_text(entry.find("a:published", ATOM_NS))
        updated = _safe_text(entry.find("a:updated", ATOM_NS))

        channel_id = ""
        author_uri = _safe_text(entry.find("a:author/a:uri", ATOM_NS))
        # Example: https://www.youtube.com/channel/UCxxxxxxxx
        if "/channel/" in author_uri:
            channel_id = author_uri.rsplit("/channel/", 1)[-1].strip()

        entries.append(
            PlaylistEntry(
                playlist_id=playlist_id,
                video_id=video_id,
                video_url=video_url,
                title=title,
                channel_id=channel_id,
                published=published,
                updated=updated,
            )
        )
    return entries


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _post_manual_hook(
    *,
    hook_url: str,
    token: str,
    entry: PlaylistEntry,
    mode: str,
    allow_degraded: bool,
    timeout_seconds: int,
) -> tuple[int, str]:
    payload = {
        "video_url": entry.video_url,
        "video_id": entry.video_id,
        "channel_id": entry.channel_id,
        "title": entry.title,
        "mode": mode,
        "allow_degraded_transcript_only": allow_degraded,
        "source": "youtube_playlist_poller",
        "playlist_id": entry.playlist_id,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(hook_url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        status = int(resp.status)
        response_body = resp.read().decode("utf-8", errors="replace")
    return status, response_body


def _playlist_ids_from_args_or_env(arg_values: list[str]) -> list[str]:
    if arg_values:
        return [v.strip() for v in arg_values if v.strip()]
    env_raw = (os.getenv("UA_YT_PLAYLIST_TRIGGER_PLAYLIST_IDS") or "").strip()
    if not env_raw:
        return []
    return [item.strip() for item in env_raw.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll YouTube playlists and forward new items to manual hook.")
    parser.add_argument(
        "--playlist-id",
        action="append",
        default=[],
        help="Playlist ID to monitor. Repeat flag for multiple playlists.",
    )
    parser.add_argument("--hook-url", default=os.getenv("UA_YT_PLAYLIST_TRIGGER_HOOK_URL", DEFAULT_HOOK_URL))
    parser.add_argument("--token", default=os.getenv("UA_HOOKS_TOKEN", ""))
    parser.add_argument("--mode", default=os.getenv("UA_YT_PLAYLIST_TRIGGER_MODE", DEFAULT_MODE))
    parser.add_argument(
        "--allow-degraded-transcript-only",
        action=argparse.BooleanOptionalAction,
        default=(os.getenv("UA_YT_PLAYLIST_TRIGGER_ALLOW_DEGRADED", "true").strip().lower() in {"1", "true", "yes", "on"}),
    )
    parser.add_argument(
        "--state-file",
        default=os.getenv("UA_YT_PLAYLIST_TRIGGER_STATE_FILE", DEFAULT_STATE_FILE),
    )
    parser.add_argument(
        "--seed-on-first-run",
        action=argparse.BooleanOptionalAction,
        default=(os.getenv("UA_YT_PLAYLIST_TRIGGER_SEED_ON_FIRST_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}),
        help="If true, first run stores current playlist items without triggering hooks.",
    )
    parser.add_argument("--feed-timeout-seconds", type=int, default=20)
    parser.add_argument("--hook-timeout-seconds", type=int, default=20)
    parser.add_argument("--max-seen-per-playlist", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true", help="Do not send hooks; only log what would happen.")
    args = parser.parse_args()

    playlist_ids = _playlist_ids_from_args_or_env(args.playlist_id)
    if not playlist_ids:
        _log("No playlist IDs configured. Set --playlist-id or UA_YT_PLAYLIST_TRIGGER_PLAYLIST_IDS.")
        return 2

    state_path = Path(args.state_file)
    state = _load_state(state_path)
    if "playlists" not in state or not isinstance(state.get("playlists"), dict):
        state["playlists"] = {}

    total_sent = 0
    total_detected = 0
    for playlist_id in playlist_ids:
        try:
            entries = _fetch_playlist_feed(playlist_id, timeout_seconds=max(5, args.feed_timeout_seconds))
        except urllib.error.HTTPError as exc:
            _log(f"Playlist {playlist_id}: feed HTTP error {exc.code}")
            continue
        except Exception as exc:
            _log(f"Playlist {playlist_id}: feed fetch error: {exc}")
            continue

        if not entries:
            _log(f"Playlist {playlist_id}: no entries found in feed.")
            continue

        playlist_state = state["playlists"].get(playlist_id) or {}
        seen: list[str] = list(playlist_state.get("seen_video_ids") or [])
        seen_set = set(seen)
        current_ids = [e.video_id for e in entries]

        # First run seeding avoids replaying the entire existing playlist.
        if not seen and args.seed_on_first_run:
            playlist_state["seen_video_ids"] = current_ids[: args.max_seen_per_playlist]
            playlist_state["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state["playlists"][playlist_id] = playlist_state
            _log(f"Playlist {playlist_id}: seeded {len(current_ids)} existing items (first run).")
            continue

        new_entries = [entry for entry in entries if entry.video_id not in seen_set]
        total_detected += len(new_entries)
        if not new_entries:
            _log(f"Playlist {playlist_id}: no new items.")
            playlist_state["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state["playlists"][playlist_id] = playlist_state
            continue

        # Process oldest first for deterministic ordering.
        for entry in reversed(new_entries):
            _log(f"Playlist {playlist_id}: new video {entry.video_id} title={entry.title!r}")
            if args.dry_run:
                continue
            try:
                status, response_body = _post_manual_hook(
                    hook_url=args.hook_url,
                    token=args.token,
                    entry=entry,
                    mode=args.mode,
                    allow_degraded=args.allow_degraded_transcript_only,
                    timeout_seconds=max(5, args.hook_timeout_seconds),
                )
            except Exception as exc:
                _log(f"Playlist {playlist_id}: hook post failed for {entry.video_id}: {exc}")
                continue

            if 200 <= status < 300:
                total_sent += 1
                _log(f"Playlist {playlist_id}: hook accepted for {entry.video_id} status={status}")
                seen.insert(0, entry.video_id)
                seen = seen[: args.max_seen_per_playlist]
                seen_set.add(entry.video_id)
            else:
                _log(f"Playlist {playlist_id}: hook rejected for {entry.video_id} status={status} body={response_body[:400]}")

        # Keep state in sync with latest feed + accepted sends.
        merged = list(dict.fromkeys(seen + current_ids))
        playlist_state["seen_video_ids"] = merged[: args.max_seen_per_playlist]
        playlist_state["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["playlists"][playlist_id] = playlist_state

    state["last_run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(state_path, state)
    _log(f"Done. detected_new={total_detected} sent={total_sent} state_file={state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
