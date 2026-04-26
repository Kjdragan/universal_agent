"""Reddit discovery adapter — polls subreddit feeds via OAuth2 API."""

from __future__ import annotations

from datetime import datetime, timezone, date
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import os

import httpx

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.adapters.reddit_oauth import RedditOAuthManager
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store.source_manager import (
    get_active_reddit_sources,
    seed_reddit_sources,
)

logger = logging.getLogger(__name__)


class RedditDiscoveryAdapter(SourceAdapter):
    """Poll subreddit feeds via Reddit OAuth2 API."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._seen_by_subreddit: dict[str, set[str]] = {}
        self._seeded_by_subreddit: dict[str, bool] = {}
        self._seed_on_first_run = bool(config.get("seed_on_first_run", True))
        self._max_seen_cache = max(100, int(config.get("max_seen_cache_per_subreddit", 2000)))
        self._watchlist_file = str(config.get("watchlist_file") or "").strip()
        self._watchlist_fallback_file = Path(__file__).resolve().parents[2] / "reddit_watchlist.json"
        self._watchlist_file_mtime: float | None = None
        self._watchlist_file_subreddits: list[str] = []
        self._db_conn: sqlite3.Connection | None = None
        self._db_seeded: bool = False
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None

        # ── OAuth2 token manager ──
        user_agent = str(config.get("user_agent") or "CSIIngester/1.0 (by u/csi_ingester)").strip()
        self._oauth = RedditOAuthManager(
            client_id=str(config.get("client_id") or os.getenv("REDDIT_CLIENT_ID", "")).strip(),
            client_secret=str(config.get("client_secret") or os.getenv("REDDIT_CLIENT_SECRET", "")).strip(),
            username=str(config.get("reddit_username") or os.getenv("REDDIT_USERNAME", "")).strip(),
            password=str(config.get("reddit_password") or os.getenv("REDDIT_PASSWORD", "")).strip(),
            user_agent=user_agent,
        )
        if self._oauth.is_configured:
            logger.info("Reddit OAuth configured — will use oauth.reddit.com")
        else:
            logger.warning("Reddit OAuth NOT configured — will fall back to unauthenticated endpoints (likely blocked)")

        # ── Proxy bandwidth metering ──
        # Default 100 MB/day budget.  Set CSI_REDDIT_DAILY_BANDWIDTH_MB to override.
        self._bw_daily_limit_bytes: int = int(
            float(config.get("daily_bandwidth_mb") or os.getenv("CSI_REDDIT_DAILY_BANDWIDTH_MB", "100"))
            * 1024 * 1024
        )
        self._bw_today: date = date.today()
        self._bw_bytes_today: int = 0
        self._bw_budget_exhausted: bool = False

    def set_state_backend(self, load_state_fn, save_state_fn) -> None:
        self._load_state_fn = load_state_fn
        self._save_state_fn = save_state_fn

    def set_db_connection(self, conn: sqlite3.Connection) -> None:
        """Set the DB connection for source management queries."""
        self._db_conn = conn

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
        # ── Try DB-backed source list first ──
        if self._db_conn is not None:
            try:
                return self._resolve_subreddits_from_db()
            except Exception as exc:
                logger.warning("DB subreddit query failed, falling back to JSON: %s", exc)

        # ── Fallback: legacy resolution ──
        raw = self.config.get("subreddits")
        out: list[str] = []
        configured_count = 0
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
                    configured_count += 1
                elif isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    if not name:
                        name = str(item.get("subreddit") or "").strip()
                    if name:
                        out.append(name)
                        configured_count += 1
        out.extend(self._load_watchlist_file_subreddits())
        deduped: list[str] = []
        seen: set[str] = set()
        for item in out:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        if not deduped:
            logger.warning(
                "Reddit watchlist resolved to zero subreddits configured_inline=%d configured_file=%s fallback_file=%s",
                configured_count,
                self._watchlist_file or "(unset)",
                self._watchlist_fallback_file,
            )
        return deduped

    def _resolve_subreddits_from_db(self) -> list[str]:
        """Resolve subreddit list from SQLite source tables."""
        assert self._db_conn is not None
        if not self._db_seeded:
            seed_path = Path(self._watchlist_file).expanduser() if self._watchlist_file else None
            if seed_path and not seed_path.exists():
                seed_path = self._watchlist_fallback_file
            if seed_path and seed_path.exists():
                count = seed_reddit_sources(self._db_conn, seed_path)
                logger.info("Reddit sources seeded from JSON: %d", count)
            self._db_seeded = True

        sources = get_active_reddit_sources(self._db_conn)
        logger.info("Reddit watchlist from DB: %d active subreddits", len(sources))
        return [src["subreddit"] for src in sources]

    def _load_watchlist_file_subreddits(self) -> list[str]:
        if not self._watchlist_file:
            return []
        path = Path(self._watchlist_file).expanduser()
        if not path.exists():
            fallback_path = self._watchlist_fallback_file
            if fallback_path.exists() and fallback_path != path:
                logger.warning(
                    "Reddit watchlist file missing path=%s; using fallback path=%s",
                    path,
                    fallback_path,
                )
                path = fallback_path
            elif self._watchlist_file_subreddits:
                logger.warning("Reddit watchlist file missing path=%s; keeping previous cached list", path)
                return self._watchlist_file_subreddits
            else:
                logger.warning("Reddit watchlist file missing path=%s and no cached subreddits available", path)
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

    def _reset_bandwidth_if_new_day(self) -> None:
        """Reset daily bandwidth counter at midnight."""
        today = date.today()
        if today != self._bw_today:
            if self._bw_bytes_today > 0:
                logger.info(
                    "Reddit proxy bandwidth day rollover: yesterday=%s used=%.2f MB / %.2f MB limit",
                    self._bw_today, self._bw_bytes_today / (1024 * 1024),
                    self._bw_daily_limit_bytes / (1024 * 1024),
                )
            self._bw_today = today
            self._bw_bytes_today = 0
            self._bw_budget_exhausted = False

    def _record_bytes(self, num_bytes: int) -> None:
        """Track bandwidth usage and emit warnings at thresholds."""
        self._bw_bytes_today += num_bytes
        used_mb = self._bw_bytes_today / (1024 * 1024)
        limit_mb = self._bw_daily_limit_bytes / (1024 * 1024)
        pct = (self._bw_bytes_today / self._bw_daily_limit_bytes * 100) if self._bw_daily_limit_bytes > 0 else 0

        if pct >= 100 and not self._bw_budget_exhausted:
            self._bw_budget_exhausted = True
            logger.critical(
                "🛑 REDDIT PROXY BANDWIDTH EXHAUSTED: %.2f MB / %.2f MB (%.0f%%). "
                "Disabling proxy-based Reddit fetching for the rest of today. "
                "Set CSI_REDDIT_DAILY_BANDWIDTH_MB to increase the limit.",
                used_mb, limit_mb, pct,
            )
        elif pct >= 90:
            logger.error(
                "⚠️ Reddit proxy bandwidth CRITICAL: %.2f MB / %.2f MB (%.0f%%)",
                used_mb, limit_mb, pct,
            )
        elif pct >= 75:
            logger.warning(
                "Reddit proxy bandwidth HIGH: %.2f MB / %.2f MB (%.0f%%)",
                used_mb, limit_mb, pct,
            )

    async def fetch_events(self) -> list[RawEvent]:
        subreddits = self._subreddits()
        timeout_seconds = max(5, int(self.config.get("timeout_seconds", 20)))
        per_subreddit_limit = max(5, min(100, int(self.config.get("limit", 50))))
        user_agent = str(self.config.get("user_agent") or "CSIIngester/1.0 (by u/csi_ingester)").strip()

        # ── Bandwidth gate ──
        self._reset_bandwidth_if_new_day()
        if self._bw_budget_exhausted:
            logger.warning(
                "Reddit fetch SKIPPED — daily proxy bandwidth budget exhausted (%.2f MB used)",
                self._bw_bytes_today / (1024 * 1024),
            )
            return []

        events: list[RawEvent] = []
        failed_subreddits = 0
        total_subreddits = len(subreddits)
        cycle_bytes = 0

        # Route through residential proxy when configured to bypass VPS IP blocks.
        # Checks CSI_REDDIT_PROXY_URL first, then CSI_RSS_PROXY_URL as shared fallback.
        proxy_url: str | None = (
            str(
                self.config.get("proxy_url")
                or os.getenv("CSI_REDDIT_PROXY_URL")
                or os.getenv("CSI_RSS_PROXY_URL")
                or ""
            ).strip() or None
        )
        client_kwargs: dict[str, Any] = {"timeout": timeout_seconds, "follow_redirects": True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(**client_kwargs) as client:
            for subreddit in subreddits:
                self._hydrate_subreddit_state(subreddit)
                # Check budget mid-cycle — stop early if exhausted
                if self._bw_budget_exhausted:
                    logger.warning("Reddit fetch stopping mid-cycle — bandwidth budget exhausted")
                    break

                try:
                    children, resp_bytes = await self._fetch_subreddit_children(
                        client,
                        subreddit=subreddit,
                        per_subreddit_limit=per_subreddit_limit,
                        user_agent=user_agent,
                    )
                    cycle_bytes += resp_bytes
                    self._record_bytes(resp_bytes)
                except Exception as exc:
                    failed_subreddits += 1
                    logger.warning("Reddit fetch failed subreddit=%s error=%s", subreddit, exc)
                    self._persist_subreddit_state(subreddit)
                    continue
                if not children:
                    self._persist_subreddit_state(subreddit)
                    continue

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

        # ── Observability: flag total poll failure ──
        if total_subreddits > 0 and failed_subreddits == total_subreddits:
            raise RuntimeError(
                f"All {total_subreddits} subreddits failed to fetch — "
                f"likely IP-blocked or OAuth misconfigured"
            )

        # ── Log cycle bandwidth summary ──
        if cycle_bytes > 0:
            logger.info(
                "Reddit poll cycle: %d subreddits, %d events, %.2f KB this cycle, "
                "%.2f MB today / %.2f MB limit (%.0f%%)",
                total_subreddits, len(events), cycle_bytes / 1024,
                self._bw_bytes_today / (1024 * 1024),
                self._bw_daily_limit_bytes / (1024 * 1024),
                (self._bw_bytes_today / self._bw_daily_limit_bytes * 100) if self._bw_daily_limit_bytes > 0 else 0,
            )

        return events

    async def _fetch_subreddit_children(
        self,
        client: httpx.AsyncClient,
        *,
        subreddit: str,
        per_subreddit_limit: int,
        user_agent: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """Returns (children, response_bytes)."""
        params: dict[str, Any] = {"limit": per_subreddit_limit, "raw_json": 1}

        # ── Primary path: OAuth API ──
        if self._oauth.is_configured:
            result, resp_bytes = await self._fetch_oauth(
                client, subreddit=subreddit, params=params, user_agent=user_agent,
            )
            if result is not None:
                return result, resp_bytes
            # OAuth failed — fall through to unauthenticated as last resort
            logger.warning(
                "Reddit OAuth fetch failed for r/%s, trying unauthenticated fallback",
                subreddit,
            )

        # ── Fallback: unauthenticated endpoints ──
        return await self._fetch_unauthenticated(
            client, subreddit=subreddit, params=params, user_agent=user_agent,
        )

    async def _fetch_oauth(
        self,
        client: httpx.AsyncClient,
        *,
        subreddit: str,
        params: dict[str, Any],
        user_agent: str,
    ) -> tuple[list[dict[str, Any]] | None, int]:
        """Fetch subreddit posts via oauth.reddit.com with bearer token.

        Returns (children | None, response_bytes).
        """
        try:
            token = await self._oauth.get_token()
        except Exception as exc:
            logger.warning("Reddit OAuth token acquisition failed: %s", exc)
            return None, 0

        url = f"https://oauth.reddit.com/r/{subreddit}/new"
        headers = {
            "User-Agent": user_agent,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            resp = await client.get(url, params=params, headers=headers)
        except Exception as exc:
            logger.warning("Reddit OAuth request failed subreddit=%s error=%s", subreddit, exc)
            return None, 0

        resp_bytes = len(resp.content) if resp.content else 0

        if resp.status_code >= 400:
            logger.warning(
                "Reddit OAuth error subreddit=%s status=%s body=%s",
                subreddit, resp.status_code, resp.text[:200],
            )
            return None, resp_bytes

        return self._parse_listing_response(resp, subreddit=subreddit), resp_bytes

    async def _fetch_unauthenticated(
        self,
        client: httpx.AsyncClient,
        *,
        subreddit: str,
        params: dict[str, Any],
        user_agent: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch subreddit posts via public JSON endpoints (legacy fallback).

        Returns (children, response_bytes).
        """
        endpoints = list(self.config.get("endpoints") or [])
        if not endpoints:
            endpoints = [
                "https://www.reddit.com/r/{subreddit}/new/.json",
                "https://old.reddit.com/r/{subreddit}/new/.json",
            ]
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        }
        last_error = ""
        total_bytes = 0
        for template in endpoints:
            url = str(template or "").strip().format(subreddit=subreddit)
            if not url:
                continue
            try:
                resp = await client.get(url, params=params, headers=headers)
            except Exception as exc:
                last_error = f"request:{type(exc).__name__}:{exc}"
                logger.warning("Reddit endpoint request failed subreddit=%s url=%s error=%s", subreddit, url, exc)
                continue
            total_bytes += len(resp.content) if resp.content else 0
            if resp.status_code >= 400:
                last_error = f"http_{resp.status_code}"
                logger.warning(
                    "Reddit endpoint error subreddit=%s url=%s status=%s",
                    subreddit,
                    url,
                    resp.status_code,
                )
                continue
            result = self._parse_listing_response(resp, subreddit=subreddit)
            if result is not None:
                return result, total_bytes
            last_error = "invalid_payload_shape"
        if last_error:
            raise RuntimeError(last_error)
        return [], total_bytes

    def _parse_listing_response(
        self, resp: httpx.Response, *, subreddit: str
    ) -> list[dict[str, Any]] | None:
        """Parse a Reddit listing response into a list of children."""
        try:
            payload = resp.json() if resp.content else {}
        except Exception as exc:
            logger.warning(
                "Reddit JSON parse failed subreddit=%s error=%s", subreddit, exc
            )
            return None
        data = payload.get("data") if isinstance(payload, dict) else {}
        children = data.get("children") if isinstance(data, dict) else []
        if isinstance(children, list):
            return children
        return None

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
