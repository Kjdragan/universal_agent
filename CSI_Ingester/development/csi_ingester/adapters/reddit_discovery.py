"""Reddit discovery adapter scaffold (disabled by default)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.contract import CreatorSignalEvent

logger = logging.getLogger(__name__)


class RedditDiscoveryAdapter(SourceAdapter):
    """Poll subreddit JSON feeds and normalize new-post signals."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._seen_by_subreddit: dict[str, set[str]] = {}
        self._seeded_by_subreddit: dict[str, bool] = {}
        self._seed_on_first_run = bool(config.get("seed_on_first_run", True))
        self._max_seen_cache = max(100, int(config.get("max_seen_cache_per_subreddit", 2000)))
        self._watchlist_file = str(config.get("watchlist_file") or "").strip()
        self._watchlist_file_mtime: float | None = None
        self._watchlist_file_subreddits: list[str] = []
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None

    def set_state_backend(self, load_state_fn, save_state_fn) -> None:
        self._load_state_fn = load_state_fn
        self._save_state_fn = save_state_fn

    def _state_key(self, subreddit: str) -> str:
        return f"reddit_discovery:{subreddit.lower()}"

    def _hydrate_subreddit_state(self, subreddit: str) -> None:
        if subreddit in self._seeded_by_subreddit:
            return
        raw = self._load_state_fn(self._state_key(subreddit))
        if not isinstance(raw, dict):
            self._seeded_by_subreddit[subreddit] = False
            return
        self._seeded_by_subreddit[subreddit] = bool(raw.get("seeded", False))
        seen_ids = raw.get("seen_ids")
        if isinstance(seen_ids, list):
            cleaned = [str(x).strip() for x in seen_ids if str(x).strip()]
            if cleaned:
                self._seen_by_subreddit[subreddit] = set(cleaned[: self._max_seen_cache])

    def _persist_subreddit_state(self, subreddit: str) -> None:
        seen = sorted(self._seen_by_subreddit.get(subreddit, set()))
        payload = {
            "seeded": bool(self._seeded_by_subreddit.get(subreddit, False)),
            "seen_ids": seen[: self._max_seen_cache],
            "updated_at": _iso_now(),
        }
        self._save_state_fn(self._state_key(subreddit), payload)

    def _subreddits(self) -> list[str]:
        raw = self.config.get("subreddits")
        out: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
                elif isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    if not name:
                        name = str(item.get("subreddit") or "").strip()
                    if name:
                        out.append(name)
        out.extend(self._load_watchlist_file_subreddits())
        deduped: list[str] = []
        seen: set[str] = set()
        for item in out:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _load_watchlist_file_subreddits(self) -> list[str]:
        if not self._watchlist_file:
            return []
        path = Path(self._watchlist_file).expanduser()
        if not path.exists():
            if self._watchlist_file_subreddits:
                logger.warning("Reddit watchlist file missing path=%s; keeping previous cached list", path)
            return self._watchlist_file_subreddits

        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.warning("Reddit watchlist file stat failed path=%s error=%s", path, exc)
            return self._watchlist_file_subreddits

        if self._watchlist_file_mtime is not None and mtime == self._watchlist_file_mtime:
            return self._watchlist_file_subreddits

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Reddit watchlist file parse failed path=%s error=%s", path, exc)
            return self._watchlist_file_subreddits

        subreddits: list[str] = []
        if isinstance(payload, dict):
            raw_subreddits = payload.get("subreddits")
            if isinstance(raw_subreddits, list):
                for row in raw_subreddits:
                    if isinstance(row, str) and row.strip():
                        subreddits.append(row.strip())
                    elif isinstance(row, dict):
                        name = str(row.get("name") or "").strip()
                        if not name:
                            name = str(row.get("subreddit") or "").strip()
                        if name:
                            subreddits.append(name)
        elif isinstance(payload, list):
            for row in payload:
                if isinstance(row, str) and row.strip():
                    subreddits.append(row.strip())
                elif isinstance(row, dict):
                    name = str(row.get("name") or "").strip()
                    if not name:
                        name = str(row.get("subreddit") or "").strip()
                    if name:
                        subreddits.append(name)

        self._watchlist_file_mtime = mtime
        self._watchlist_file_subreddits = subreddits
        logger.info("Reddit watchlist loaded path=%s subreddits=%d", path, len(subreddits))
        return self._watchlist_file_subreddits

    async def fetch_events(self) -> list[RawEvent]:
        subreddits = self._subreddits()
        timeout_seconds = max(5, int(self.config.get("timeout_seconds", 20)))
        per_subreddit_limit = max(5, min(100, int(self.config.get("limit", 50))))
        user_agent = str(self.config.get("user_agent") or "CSIIngester/1.0 (by u/csi_ingester)").strip()

        events: list[RawEvent] = []
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            for subreddit in subreddits:
                self._hydrate_subreddit_state(subreddit)
                url = f"https://www.reddit.com/r/{subreddit}/new.json"
                resp = await client.get(
                    url,
                    params={"limit": per_subreddit_limit},
                    headers={"User-Agent": user_agent, "Accept": "application/json"},
                )
                if resp.status_code >= 400:
                    self._persist_subreddit_state(subreddit)
                    continue

                payload = resp.json() if resp.content else {}
                data = payload.get("data") if isinstance(payload, dict) else {}
                children = data.get("children") if isinstance(data, dict) else []
                if not isinstance(children, list):
                    children = []

                items: list[dict[str, Any]] = []
                for child in children:
                    row = child.get("data") if isinstance(child, dict) else None
                    if not isinstance(row, dict):
                        continue
                    post_id = str(row.get("id") or "").strip()
                    if not post_id:
                        continue
                    created_utc = float(row.get("created_utc") or 0)
                    occurred_at = _iso_from_epoch(created_utc) if created_utc > 0 else _iso_now()
                    permalink = str(row.get("permalink") or "").strip()
                    items.append(
                        {
                            "post_id": post_id,
                            "subreddit": str(row.get("subreddit") or subreddit).strip() or subreddit,
                            "title": str(row.get("title") or "").strip(),
                            "url": str(row.get("url") or "").strip(),
                            "permalink": permalink,
                            "author": str(row.get("author") or "").strip(),
                            "score": int(row.get("score") or 0),
                            "num_comments": int(row.get("num_comments") or 0),
                            "occurred_at": occurred_at,
                        }
                    )

                seen = self._seen_by_subreddit.setdefault(subreddit, set())
                post_ids = [item["post_id"] for item in items]
                seeded = self._seeded_by_subreddit.get(subreddit, False)
                if not seen and self._seed_on_first_run and not seeded:
                    seen.update(post_ids)
                    self._seeded_by_subreddit[subreddit] = True
                    self._persist_subreddit_state(subreddit)
                    continue

                new_items = [item for item in items if item["post_id"] not in seen]
                for item in reversed(new_items):
                    events.append(
                        RawEvent(
                            source="reddit_discovery",
                            event_type="subreddit_new_post",
                            payload=item,
                            occurred_at=str(item.get("occurred_at") or _iso_now()),
                        )
                    )
                    seen.add(item["post_id"])

                self._seeded_by_subreddit[subreddit] = True
                self._seen_by_subreddit[subreddit] = set(post_ids[: self._max_seen_cache])
                self._persist_subreddit_state(subreddit)

        return events

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        now = _iso_now()
        payload = raw.payload if isinstance(raw.payload, dict) else {}
        post_id = str(payload.get("post_id") or "")
        subreddit = str(payload.get("subreddit") or "").strip()
        permalink = str(payload.get("permalink") or "").strip()
        permalink_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink

        event = CreatorSignalEvent(
            event_id=f"reddit:new:{subreddit}:{post_id}:{int(datetime.now(timezone.utc).timestamp())}",
            dedupe_key="",
            source="reddit_discovery",
            event_type="subreddit_new_post",
            occurred_at=str(raw.occurred_at or now),
            received_at=now,
            subject={
                "platform": "reddit",
                "subreddit": subreddit,
                "post_id": post_id,
                "title": str(payload.get("title") or ""),
                "url": str(payload.get("url") or ""),
                "permalink": permalink_url,
                "author": str(payload.get("author") or ""),
                "score": int(payload.get("score") or 0),
                "num_comments": int(payload.get("num_comments") or 0),
            },
            routing={
                "pipeline": "creator_watchlist_handler",
                "priority": "standard",
                "tags": ["reddit", "watchlist"],
            },
            metadata={"source_adapter": "reddit_discovery_v1"},
        )
        event.dedupe_key = self.get_dedupe_key(event)
        return event

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        post_id = str(event.subject.get("post_id") or "")
        return f"reddit:post:{post_id}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_from_epoch(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
