"""Threads API client helpers for CSI adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_THREADS_BASE_URL_DEFAULT = "https://graph.threads.net/v1.0"
_THREADS_TOKEN_URL_DEFAULT = "https://graph.threads.net"


class ThreadsAPIError(RuntimeError):
    """Raised when Threads API requests fail after retries."""


class ThreadsRateLimitError(ThreadsAPIError):
    """Raised when local quota budget denies an API call."""


@dataclass(slots=True)
class ThreadsQuotaWindow:
    max_requests: int
    window_seconds: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_from_epoch(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _http_error_detail(resp: httpx.Response) -> str:
    detail = (resp.text or "").strip().replace("\n", " ")
    if len(detail) > 400:
        detail = detail[:400]
    trace = str(resp.headers.get("x-fb-trace-id") or resp.headers.get("x-request-id") or "").strip()
    if trace and detail:
        return f"{detail} trace_id={trace}"
    if trace:
        return f"trace_id={trace}"
    return detail


class ThreadsQuotaBudgetManager:
    """Local rolling-window quota accounting per endpoint."""

    DEFAULT_BUDGETS: dict[str, ThreadsQuotaWindow] = {
        "user_threads": ThreadsQuotaWindow(max_requests=1200, window_seconds=86_400),
        "mentions": ThreadsQuotaWindow(max_requests=1200, window_seconds=86_400),
        "replies": ThreadsQuotaWindow(max_requests=1200, window_seconds=86_400),
        "insights": ThreadsQuotaWindow(max_requests=1200, window_seconds=86_400),
        "keyword_search": ThreadsQuotaWindow(max_requests=2200, window_seconds=86_400),
        "profile_posts": ThreadsQuotaWindow(max_requests=1000, window_seconds=86_400),
        "publishing": ThreadsQuotaWindow(max_requests=300, window_seconds=86_400),
    }

    def __init__(self, budgets: dict[str, ThreadsQuotaWindow], state: dict[str, Any] | None = None) -> None:
        self._budgets = budgets
        self._hits: dict[str, list[float]] = {}
        self._hydrate(state or {})

    @classmethod
    def from_config(cls, quota_config: Any, state: dict[str, Any] | None = None) -> "ThreadsQuotaBudgetManager":
        budgets: dict[str, ThreadsQuotaWindow] = dict(cls.DEFAULT_BUDGETS)
        if isinstance(quota_config, dict):
            for endpoint, raw in quota_config.items():
                if not isinstance(endpoint, str) or not endpoint.strip() or not isinstance(raw, dict):
                    continue
                max_requests = max(1, _safe_int(raw.get("max_requests"), budgets.get(endpoint, ThreadsQuotaWindow(500, 3600)).max_requests))
                window_seconds = max(60, _safe_int(raw.get("window_seconds"), budgets.get(endpoint, ThreadsQuotaWindow(500, 3600)).window_seconds))
                budgets[endpoint.strip()] = ThreadsQuotaWindow(max_requests=max_requests, window_seconds=window_seconds)
        return cls(budgets=budgets, state=state)

    def _hydrate(self, state: dict[str, Any]) -> None:
        hits = state.get("hits")
        if not isinstance(hits, dict):
            return
        now = _utc_now().timestamp()
        for endpoint, raw in hits.items():
            budget = self._budgets.get(str(endpoint))
            if budget is None or not isinstance(raw, list):
                continue
            cleaned: list[float] = []
            cutoff = now - float(budget.window_seconds)
            for item in raw:
                try:
                    ts = float(item)
                except Exception:
                    continue
                if ts >= cutoff:
                    cleaned.append(ts)
            if cleaned:
                self._hits[str(endpoint)] = cleaned

    def allow(self, endpoint: str, *, cost: int = 1, now_epoch: float | None = None) -> bool:
        endpoint_key = str(endpoint or "").strip()
        if not endpoint_key:
            return True
        budget = self._budgets.get(endpoint_key)
        if budget is None:
            return True
        timestamp = float(now_epoch) if now_epoch is not None else _utc_now().timestamp()
        records = self._hits.setdefault(endpoint_key, [])
        cutoff = timestamp - float(budget.window_seconds)
        records[:] = [ts for ts in records if ts >= cutoff]
        units = max(1, int(cost))
        if len(records) + units > int(budget.max_requests):
            return False
        records.extend([timestamp] * units)
        return True

    def remaining(self, endpoint: str, *, now_epoch: float | None = None) -> int:
        endpoint_key = str(endpoint or "").strip()
        budget = self._budgets.get(endpoint_key)
        if budget is None:
            return 2**31 - 1
        timestamp = float(now_epoch) if now_epoch is not None else _utc_now().timestamp()
        records = self._hits.setdefault(endpoint_key, [])
        cutoff = timestamp - float(budget.window_seconds)
        records[:] = [ts for ts in records if ts >= cutoff]
        return max(0, int(budget.max_requests) - len(records))

    def to_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "updated_at": _iso_now(),
            "hits": {
                endpoint: [float(ts) for ts in timestamps]
                for endpoint, timestamps in self._hits.items()
                if timestamps
            },
        }

    def to_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        now = _utc_now().timestamp()
        for endpoint, budget in self._budgets.items():
            remaining = self.remaining(endpoint, now_epoch=now)
            out[endpoint] = {
                "remaining": int(remaining),
                "max_requests": int(budget.max_requests),
                "window_seconds": int(budget.window_seconds),
            }
        return out


class ThreadsTokenManager:
    """Threads token exchange/refresh helper."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        access_token: str,
        token_expires_at: str = "",
        token_url: str = _THREADS_TOKEN_URL_DEFAULT,
        refresh_buffer_seconds: int = 6 * 3600,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.access_token = str(access_token or "").strip()
        self.token_expires_at = str(token_expires_at or "").strip()
        self.token_url = str(token_url or _THREADS_TOKEN_URL_DEFAULT).rstrip("/")
        self.refresh_buffer_seconds = max(60, int(refresh_buffer_seconds))

    @classmethod
    def from_env(cls) -> "ThreadsTokenManager":
        return cls(
            app_id=str(os.getenv("THREADS_APP_ID") or "").strip(),
            app_secret=str(os.getenv("THREADS_APP_SECRET") or "").strip(),
            access_token=str(os.getenv("THREADS_ACCESS_TOKEN") or "").strip(),
            token_expires_at=str(os.getenv("THREADS_TOKEN_EXPIRES_AT") or "").strip(),
        )

    def is_configured(self) -> bool:
        return bool(self.access_token)

    def is_expiring_soon(self, *, now: datetime | None = None) -> bool:
        if not self.token_expires_at:
            return False
        expiry = _parse_iso(self.token_expires_at)
        if expiry is None:
            return False
        now_dt = now or _utc_now()
        return expiry <= (now_dt + timedelta(seconds=self.refresh_buffer_seconds))

    async def exchange_short_lived_token(
        self,
        short_lived_token: str,
        *,
        timeout_seconds: int = 20,
    ) -> tuple[str, str]:
        token = str(short_lived_token or "").strip()
        if not token:
            raise ThreadsAPIError("short_lived_token_required")
        params = {
            "grant_type": "th_exchange_token",
            "client_secret": self.app_secret,
            "access_token": token,
        }
        async with httpx.AsyncClient(timeout=max(5, int(timeout_seconds))) as client:
            resp = await client.get(f"{self.token_url}/access_token", params=params)
        if resp.status_code >= 400:
            raise ThreadsAPIError(f"token_exchange_http_{resp.status_code}")
        payload = resp.json() if resp.content else {}
        new_token = str(payload.get("access_token") or "").strip()
        expires_in = max(0, _safe_int(payload.get("expires_in"), 0))
        if not new_token:
            raise ThreadsAPIError("token_exchange_missing_access_token")
        expires_at = _iso_from_epoch(_utc_now().timestamp() + float(expires_in)) if expires_in > 0 else ""
        self.access_token = new_token
        if expires_at:
            self.token_expires_at = expires_at
        return new_token, expires_at

    async def refresh_long_lived_token(self, *, timeout_seconds: int = 20) -> tuple[str, str]:
        if not self.access_token:
            raise ThreadsAPIError("threads_access_token_missing")
        params = {
            "grant_type": "th_refresh_token",
            "access_token": self.access_token,
        }
        async with httpx.AsyncClient(timeout=max(5, int(timeout_seconds))) as client:
            resp = await client.get(f"{self.token_url}/refresh_access_token", params=params)
        if resp.status_code >= 400:
            raise ThreadsAPIError(f"token_refresh_http_{resp.status_code}")
        payload = resp.json() if resp.content else {}
        refreshed_token = str(payload.get("access_token") or self.access_token).strip()
        expires_in = max(0, _safe_int(payload.get("expires_in"), 0))
        expires_at = _iso_from_epoch(_utc_now().timestamp() + float(expires_in)) if expires_in > 0 else self.token_expires_at
        self.access_token = refreshed_token
        self.token_expires_at = expires_at
        return refreshed_token, expires_at


class ThreadsAPIClient:
    """Minimal Threads API client used by CSI adapters."""

    def __init__(
        self,
        *,
        user_id: str,
        token_manager: ThreadsTokenManager,
        quota_manager: ThreadsQuotaBudgetManager,
        base_url: str = _THREADS_BASE_URL_DEFAULT,
        timeout_seconds: int = 20,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.8,
        user_agent: str = "CSIIngester/threads-v1",
    ) -> None:
        self.user_id = str(user_id or "").strip()
        self.token_manager = token_manager
        self.quota_manager = quota_manager
        self.base_url = str(base_url or _THREADS_BASE_URL_DEFAULT).rstrip("/")
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff_seconds = max(0.1, float(retry_backoff_seconds))
        self.user_agent = str(user_agent or "CSIIngester/threads-v1").strip()

    @classmethod
    def from_config(cls, config: dict[str, Any], *, quota_state: dict[str, Any] | None = None) -> "ThreadsAPIClient":
        token_manager = ThreadsTokenManager(
            app_id=str(os.getenv("THREADS_APP_ID") or config.get("app_id") or "").strip(),
            app_secret=str(os.getenv("THREADS_APP_SECRET") or config.get("app_secret") or "").strip(),
            access_token=str(os.getenv("THREADS_ACCESS_TOKEN") or config.get("access_token") or "").strip(),
            token_expires_at=str(os.getenv("THREADS_TOKEN_EXPIRES_AT") or config.get("token_expires_at") or "").strip(),
            token_url=str(config.get("token_url") or _THREADS_TOKEN_URL_DEFAULT),
            refresh_buffer_seconds=max(60, _safe_int(config.get("token_refresh_buffer_seconds"), 6 * 3600)),
        )
        quota_manager = ThreadsQuotaBudgetManager.from_config(config.get("quota_budgets"), state=quota_state)
        return cls(
            user_id=str(os.getenv("THREADS_USER_ID") or config.get("user_id") or "").strip(),
            token_manager=token_manager,
            quota_manager=quota_manager,
            base_url=str(config.get("base_url") or _THREADS_BASE_URL_DEFAULT),
            timeout_seconds=max(5, _safe_int(config.get("timeout_seconds"), 20)),
            max_retries=max(1, _safe_int(config.get("max_retries"), 3)),
            retry_backoff_seconds=max(0.1, float(config.get("retry_backoff_seconds") or 0.8)),
            user_agent=str(config.get("user_agent") or "CSIIngester/threads-v1"),
        )

    def is_configured(self) -> bool:
        return bool(self.user_id) and self.token_manager.is_configured()

    async def maybe_refresh_token(self) -> bool:
        if not self.token_manager.is_expiring_soon():
            return False
        try:
            await self.token_manager.refresh_long_lived_token(timeout_seconds=self.timeout_seconds)
            return True
        except Exception as exc:
            logger.warning("Threads token refresh failed error=%s", exc)
            return False

    def quota_state(self) -> dict[str, Any]:
        return self.quota_manager.to_state()

    async def get_user_threads(self, *, limit: int = 25, fields: str | None = None) -> list[dict[str, Any]]:
        if not self.user_id:
            return []
        response = await self._get(
            f"/{self.user_id}/threads",
            endpoint="user_threads",
            params={
                "limit": max(1, min(int(limit), 100)),
                "fields": fields
                or "id,text,timestamp,permalink,media_type,shortcode,username,reply_count,repost_count,quote_count,like_count",
            },
        )
        return _extract_data_list(response)

    async def get_mentions(self, *, limit: int = 25, fields: str | None = None) -> list[dict[str, Any]]:
        if not self.user_id:
            return []
        response = await self._get(
            f"/{self.user_id}/mentions",
            endpoint="mentions",
            params={
                "limit": max(1, min(int(limit), 100)),
                "fields": fields
                or "id,text,timestamp,media_type,permalink,shortcode,username,reply_count,repost_count,quote_count,like_count",
            },
        )
        return _extract_data_list(response)

    async def get_replies(self, media_id: str, *, limit: int = 25, fields: str | None = None) -> list[dict[str, Any]]:
        post_id = str(media_id or "").strip()
        if not post_id:
            return []
        response = await self._get(
            f"/{post_id}/replies",
            endpoint="replies",
            params={
                "limit": max(1, min(int(limit), 100)),
                "fields": fields
                or "id,text,timestamp,permalink,username,reply_count,repost_count,quote_count,like_count",
            },
        )
        return _extract_data_list(response)

    async def get_media_insights(self, media_id: str, *, metrics: list[str] | None = None) -> dict[str, Any]:
        post_id = str(media_id or "").strip()
        if not post_id:
            return {}
        metric_list = metrics or ["views", "likes", "replies", "reposts", "quotes", "shares"]
        response = await self._get(
            f"/{post_id}/insights",
            endpoint="insights",
            params={"metric": ",".join(sorted({str(item).strip() for item in metric_list if str(item).strip()}))},
        )
        return response

    async def keyword_search(
        self,
        *,
        query: str,
        search_type: str,
        search_surface: str = "KEYWORD",
        media_types: list[str] | None = None,
        limit: int = 30,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return []
        media_filter = [str(item).strip().upper() for item in (media_types or []) if str(item).strip()]
        params: dict[str, Any] = {
            "q": cleaned_query,
            "search_type": str(search_type or "TOP").strip().upper() or "TOP",
            "search_surface": str(search_surface or "KEYWORD").strip().upper() or "KEYWORD",
            "limit": max(1, min(int(limit), 50)),
            "fields": fields
            or "id,text,timestamp,media_type,permalink,shortcode,username,reply_count,repost_count,quote_count,like_count",
        }
        if media_filter:
            params["media_type"] = ",".join(media_filter)
        response = await self._get("/keyword_search", endpoint="keyword_search", params=params)
        return _extract_data_list(response)

    async def profile_posts(self, *, username: str, limit: int = 25, fields: str | None = None) -> list[dict[str, Any]]:
        cleaned = str(username or "").strip()
        if not cleaned:
            return []
        response = await self._get(
            "/profile_posts",
            endpoint="profile_posts",
            params={
                "username": cleaned,
                "limit": max(1, min(int(limit), 100)),
                "fields": fields
                or "id,text,timestamp,media_type,permalink,shortcode,username,reply_count,repost_count,quote_count,like_count",
            },
        )
        return _extract_data_list(response)

    async def create_media_container(
        self,
        *,
        media_type: str,
        text: str = "",
        image_url: str = "",
        video_url: str = "",
        is_carousel_item: bool = False,
        children: list[str] | None = None,
        reply_to_id: str = "",
        reply_control: str = "",
        allowlisted_country_codes: list[str] | None = None,
        alt_text: str = "",
        link_attachment: str = "",
        quote_post_id: str = "",
    ) -> dict[str, Any]:
        if not self.user_id:
            raise ThreadsAPIError("threads_user_id_missing")

        mt = str(media_type or "").strip().upper()
        if mt not in {"TEXT", "IMAGE", "VIDEO", "CAROUSEL"}:
            raise ThreadsAPIError(f"invalid_media_type:{mt or 'empty'}")
        if mt == "TEXT" and not str(text or "").strip():
            raise ThreadsAPIError("text_required_for_text_media")
        if mt == "IMAGE" and not str(image_url or "").strip():
            raise ThreadsAPIError("image_url_required_for_image_media")
        if mt == "VIDEO" and not str(video_url or "").strip():
            raise ThreadsAPIError("video_url_required_for_video_media")
        if mt == "CAROUSEL":
            child_items = [str(item or "").strip() for item in (children or []) if str(item or "").strip()]
            if not child_items:
                raise ThreadsAPIError("children_required_for_carousel_media")
        else:
            child_items = []

        params: dict[str, Any] = {
            "media_type": mt,
            "text": str(text or "").strip(),
            "image_url": str(image_url or "").strip(),
            "video_url": str(video_url or "").strip(),
            "is_carousel_item": "true" if bool(is_carousel_item) else "",
            "children": ",".join(child_items),
            "reply_to_id": str(reply_to_id or "").strip(),
            "reply_control": str(reply_control or "").strip(),
            "allowlisted_country_codes": ",".join(
                str(code or "").strip().upper() for code in (allowlisted_country_codes or []) if str(code or "").strip()
            ),
            "alt_text": str(alt_text or "").strip(),
            "link_attachment": str(link_attachment or "").strip(),
            "quote_post_id": str(quote_post_id or "").strip(),
        }
        return await self._post(f"/{self.user_id}/threads", endpoint="publishing", params=params)

    async def publish_media_container(self, *, creation_id: str) -> dict[str, Any]:
        if not self.user_id:
            raise ThreadsAPIError("threads_user_id_missing")
        clean_creation_id = str(creation_id or "").strip()
        if not clean_creation_id:
            raise ThreadsAPIError("creation_id_required")
        return await self._post(
            f"/{self.user_id}/threads_publish",
            endpoint="publishing",
            params={"creation_id": clean_creation_id},
        )

    async def container_status(self, *, container_id: str) -> dict[str, Any]:
        clean_id = str(container_id or "").strip()
        if not clean_id:
            raise ThreadsAPIError("threads_container_id_required")
        return await self._get(
            f"/{clean_id}",
            endpoint="publishing",
            params={"fields": "id,status,error_message"},
        )

    async def _get(self, path: str, *, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.maybe_refresh_token()
        token = str(self.token_manager.access_token or "").strip()
        if not token:
            raise ThreadsAPIError("threads_access_token_missing")
        if not self.quota_manager.allow(endpoint, cost=1):
            raise ThreadsRateLimitError(f"quota_exhausted:{endpoint}")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        request_params = {k: v for k, v in params.items() if v is not None and str(v) != ""}
        url = f"{self.base_url}{path}"
        last_error = ""
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = await client.get(url, params=request_params, headers=headers)
                except Exception as exc:
                    last_error = f"request:{type(exc).__name__}:{exc}"
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(self.retry_backoff_seconds * float(attempt))
                    continue

                if resp.status_code in {429, 500, 502, 503, 504}:
                    detail = _http_error_detail(resp)
                    last_error = f"http_{resp.status_code}:{detail}" if detail else f"http_{resp.status_code}"
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(self.retry_backoff_seconds * float(attempt))
                    continue

                if resp.status_code >= 400:
                    detail = _http_error_detail(resp)
                    raise ThreadsAPIError(
                        f"threads_http_{resp.status_code}:{detail}" if detail else f"threads_http_{resp.status_code}"
                    )

                try:
                    return resp.json() if resp.content else {}
                except Exception as exc:
                    last_error = f"json:{type(exc).__name__}:{exc}"
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(self.retry_backoff_seconds * float(attempt))
                    continue

        raise ThreadsAPIError(last_error or f"threads_request_failed:{endpoint}")

    async def _post(self, path: str, *, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.maybe_refresh_token()
        token = str(self.token_manager.access_token or "").strip()
        if not token:
            raise ThreadsAPIError("threads_access_token_missing")
        if not self.quota_manager.allow(endpoint, cost=1):
            raise ThreadsRateLimitError(f"quota_exhausted:{endpoint}")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        request_params = {k: v for k, v in params.items() if v is not None and str(v) != ""}
        url = f"{self.base_url}{path}"
        last_error = ""
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = await client.post(url, data=request_params, headers=headers)
                except Exception as exc:
                    last_error = f"request:{type(exc).__name__}:{exc}"
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(self.retry_backoff_seconds * float(attempt))
                    continue

                if resp.status_code in {429, 500, 502, 503, 504}:
                    detail = _http_error_detail(resp)
                    last_error = f"http_{resp.status_code}:{detail}" if detail else f"http_{resp.status_code}"
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(self.retry_backoff_seconds * float(attempt))
                    continue

                if resp.status_code >= 400:
                    detail = _http_error_detail(resp)
                    raise ThreadsAPIError(
                        f"threads_http_{resp.status_code}:{detail}" if detail else f"threads_http_{resp.status_code}"
                    )

                try:
                    return resp.json() if resp.content else {}
                except Exception as exc:
                    last_error = f"json:{type(exc).__name__}:{exc}"
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(self.retry_backoff_seconds * float(attempt))
                    continue

        raise ThreadsAPIError(last_error or f"threads_request_failed:{endpoint}")


def _extract_data_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def normalize_threads_item(
    item: dict[str, Any],
    *,
    term: str = "",
    trend_bucket: str = "",
    search_type: str = "",
) -> dict[str, Any]:
    media_id = str(item.get("id") or item.get("media_id") or item.get("post_id") or "").strip()
    text = str(item.get("text") or item.get("caption") or "").strip()
    timestamp = str(item.get("timestamp") or item.get("created_at") or _iso_now()).strip()
    if not _parse_iso(timestamp):
        timestamp = _iso_now()

    base = {
        "media_id": media_id,
        "text": text,
        "timestamp": timestamp,
        "username": str(item.get("username") or "").strip(),
        "permalink": str(item.get("permalink") or "").strip(),
        "media_type": str(item.get("media_type") or "").strip(),
        "shortcode": str(item.get("shortcode") or "").strip(),
        "reply_count": _safe_int(item.get("reply_count"), 0),
        "repost_count": _safe_int(item.get("repost_count"), 0),
        "quote_count": _safe_int(item.get("quote_count"), 0),
        "like_count": _safe_int(item.get("like_count"), 0),
    }
    if term:
        base["query_term"] = term
    if trend_bucket:
        base["trend_bucket"] = trend_bucket
    if search_type:
        base["search_type"] = search_type
    return base


def insights_to_metric_map(payload: Any) -> dict[str, int]:
    rows = _extract_data_list(payload)
    metrics: dict[str, int] = {}
    for row in rows:
        name = str(row.get("name") or "").strip().lower()
        if not name:
            continue
        value = row.get("value")
        if value is None:
            values = row.get("values")
            if isinstance(values, list) and values:
                first = values[0]
                if isinstance(first, dict):
                    value = first.get("value")
        metrics[name] = _safe_int(value, 0)
    return metrics


def engagement_score(item: dict[str, Any]) -> float:
    views = float(_safe_int(item.get("views"), 0))
    likes = float(_safe_int(item.get("like_count"), 0))
    replies = float(_safe_int(item.get("reply_count"), 0))
    reposts = float(_safe_int(item.get("repost_count"), 0))
    quotes = float(_safe_int(item.get("quote_count"), 0))
    shares = float(_safe_int(item.get("share_count"), _safe_int(item.get("shares"), 0)))
    return (views * 0.05) + likes + (replies * 1.4) + (reposts * 1.2) + (quotes * 1.3) + (shares * 1.1)


def velocity_score(item: dict[str, Any], *, now: datetime | None = None) -> float:
    now_dt = now or _utc_now()
    occurred = _parse_iso(item.get("timestamp")) or now_dt
    age_minutes = max(1.0, (now_dt - occurred).total_seconds() / 60.0)
    score = engagement_score(item)
    return round(score / age_minutes, 6)


def stable_hash(parts: list[str]) -> str:
    joined = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def verify_threads_signature(*, raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    signature = str(signature_header or "").strip()
    if signature.startswith("sha256="):
        signature = signature.split("=", 1)[1].strip()
    if not signature or not app_secret:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
