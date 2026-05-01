"""Claude Code intelligence lane backed by the X API.

The lane is intentionally read-only. It polls a configured X account, writes a
durable packet for every run, deduplicates by stable X post ID, and queues
Task Hub follow-up only for posts that look implementation-worthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from universal_agent import task_hub
from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_CANDIDATE,
    ARTIFACT_STATUS_PRODUCED,
    make_artifact_id,
    upsert_artifact,
)
from universal_agent.utils.model_resolution import resolve_sonnet

DEFAULT_HANDLE = "ClaudeDevs"
DEFAULT_HANDLES = ["ClaudeDevs", "bcherny"]
DEFAULT_MAX_RESULTS = 25
SOURCE_KIND_UPDATE = "claude_code_update"
SOURCE_KIND_DEMO_TASK = "claude_code_demo_task"
SOURCE_KIND_KB_UPDATE = "claude_code_kb_update"
LANE_SLUG = "claude_code_intel"
KB_SLUG = "claude-code-intelligence"

_TRUTHY = {"1", "true", "yes", "on"}
_URL_RE = re.compile(r"https?://[^\s<>)\"']+", re.IGNORECASE)
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9_.-]+)?\b")

_TIER4_TERMS = {
    "breaking",
    "migration",
    "migrate",
    "deprecated",
    "deprecation",
    "security",
    "vulnerability",
    "outage",
    "hotfix",
    "bug",
    "regression",
}
_TIER3_TERMS = {
    "api",
    "sdk",
    "mcp",
    "hook",
    "plugin",
    "demo",
    "example",
    "repo",
    "build",
    "workflow",
    "tool",
    "feature",
    "release",
}
_TIER2_TERMS = {
    "docs",
    "documentation",
    "guide",
    "changelog",
    "release notes",
    "blog",
    "deep dive",
    "update",
    "learn",
    "skill",
}
_COMMUNITY_EVENT_TERMS = {
    "hackathon",
    "applications are open",
    "build week",
    "join us",
    "prize pool",
    "alongside developers",
    "virtual hackathon",
}

logger = logging.getLogger(__name__)

_TIER_CLASSIFIER_SYSTEM = """\
You are classifying a Claude Code intelligence post for a proactive engineering system.

Choose exactly one action type and tier:
- tier 1 / digest: informational or community chatter with low direct implementation value
- tier 2 / kb_update: docs, reference material, usage hints, release notes, or important but non-actionable product updates
- tier 3 / demo_task: concrete implementation opportunity, code-worthy feature, repo/example/API capability, or a thing worth building/testing
- tier 4 / strategic_follow_up: migration risk, breaking change, bug, remediation, safety/quality issue, or strategic operational consequence

Important:
- Do NOT classify generic event/community posts as demo_task unless they clearly imply a concrete engineering build opportunity beyond attendance/announcement.
- If the post is about applications, hackathons, or community announcements, prefer digest or kb_update unless there is a direct technical implementation implication.
- If a bug, migration issue, stale prompt, safety problem, or operational warning is described, prefer strategic_follow_up.

Return ONLY JSON:
{
  "tier": 1 | 2 | 3 | 4,
  "action_type": "digest" | "kb_update" | "demo_task" | "strategic_follow_up",
  "content_kind": "community_event" | "docs_reference" | "product_capability" | "migration_risk" | "code_example" | "usage_tip" | "generic_update",
  "confidence": "high" | "medium" | "low",
  "reasoning": "short explanation"
}
"""


@dataclass(frozen=True)
class ClaudeCodeIntelConfig:
    handle: str = DEFAULT_HANDLE
    max_results: int = DEFAULT_MAX_RESULTS
    queue_task_hub: bool = True
    request_timeout_seconds: float = 20.0
    artifacts_root: Path | None = None

    @classmethod
    def from_env(cls) -> "ClaudeCodeIntelConfig":
        return cls(
            handle=(os.getenv("UA_CLAUDE_CODE_INTEL_X_HANDLE") or DEFAULT_HANDLE).strip().lstrip("@") or DEFAULT_HANDLE,
            max_results=_bounded_int(os.getenv("UA_CLAUDE_CODE_INTEL_MAX_RESULTS"), DEFAULT_MAX_RESULTS, low=5, high=100),
            queue_task_hub=str(os.getenv("UA_CLAUDE_CODE_INTEL_QUEUE_TASKS", "1")).strip().lower() in _TRUTHY,
            request_timeout_seconds=float(os.getenv("UA_CLAUDE_CODE_INTEL_TIMEOUT_SECONDS", "20") or 20),
        )

    @classmethod
    def all_handles_from_env(cls) -> list[str]:
        """Return all configured handles from env or default list."""
        env_val = str(os.getenv("UA_CLAUDE_CODE_INTEL_X_HANDLES") or "").strip()
        if env_val:
            handles = [h.strip().lstrip("@") for h in env_val.split(",") if h.strip()]
            return handles or list(DEFAULT_HANDLES)
        return list(DEFAULT_HANDLES)


@dataclass
class ClaudeCodeIntelRun:
    ok: bool
    generated_at: str
    handle: str
    user_id: str = ""
    auth_mode: str = ""
    packet_dir: str = ""
    new_post_count: int = 0
    seen_post_count: int = 0
    action_count: int = 0
    queued_task_count: int = 0
    artifact_id: str = ""
    error: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)


def resolve_lane_root(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "proactive" / LANE_SLUG


def resolve_reference_kb_root(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "knowledge-bases" / KB_SLUG


def get_x_bearer_token() -> str:
    """Return the configured X API bearer token without logging or printing it."""
    for key in ("X_BEARER_TOKEN", "BEARER_TOKEN"):
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def run_sync(
    *,
    config: ClaudeCodeIntelConfig | None = None,
    bearer_token: str | None = None,
    conn: sqlite3.Connection | None = None,
    client: httpx.Client | None = None,
) -> ClaudeCodeIntelRun:
    start_time = time.time()
    # Allow 25 minutes to safely stay under the 30-minute cron timeout
    max_run_time_seconds = 25 * 60

    cfg = config or ClaudeCodeIntelConfig.from_env()
    generated_at = _now_iso()
    lane_root = resolve_lane_root(cfg.artifacts_root)
    packet_dir = _new_packet_dir(lane_root, handle=cfg.handle, generated_at=generated_at)
    packet_dir.mkdir(parents=True, exist_ok=True)
    if conn is not None and conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    state_path = resolve_state_path(lane_root, handle=cfg.handle)
    state = _load_state(state_path)
    token = bearer_token if bearer_token is not None else get_x_bearer_token()
    run = ClaudeCodeIntelRun(ok=False, generated_at=generated_at, handle=cfg.handle, packet_dir=str(packet_dir))

    user_payload: dict[str, Any] = {}
    posts_payload: dict[str, Any] = {}
    new_posts: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    try:
        if not token and not _has_user_auth_fallback():
            raise RuntimeError("missing X_BEARER_TOKEN or X user-context auth")
        owns_client = client is None
        http_client = client or httpx.Client(timeout=cfg.request_timeout_seconds)
        try:
            user_payload = fetch_user_by_username_with_fallbacks(http_client, token=token, username=cfg.handle)
            auth_mode = str(user_payload.pop("_ua_auth_mode", "") or "")
            user_id = str((user_payload.get("data") or {}).get("id") or "").strip()
            if not user_id:
                raise RuntimeError("X user lookup response did not include data.id")
            since_id = str(state.get("last_seen_post_id") or "").strip() or None
            posts_payload = fetch_user_posts_with_fallbacks(
                http_client,
                token=token,
                user_id=user_id,
                max_results=cfg.max_results,
                since_id=since_id,
            )
            auth_mode = str(posts_payload.pop("_ua_auth_mode", "") or auth_mode)
        finally:
            if owns_client:
                http_client.close()

        posts = normalize_posts(posts_payload)
        seen_ids = {str(v) for v in state.get("seen_post_ids", []) if str(v)}
        
        all_new_posts = [post for post in posts if str(post.get("id") or "") not in seen_ids]
        
        # Sort oldest-first so we make forward progress
        try:
            all_new_posts.sort(key=lambda x: int(x.get("id") or 0))
        except ValueError:
            pass
            
        url_enrichment_enabled = str(os.getenv("UA_CSI_URL_ENRICHMENT_ENABLED", "1")).strip().lower() in _TRUTHY

        queued = 0
        artifact_id = ""
        current_state = state
        
        chunk_size = 10
        total_chunks = (len(all_new_posts) + chunk_size - 1) // chunk_size

        for chunk_idx in range(total_chunks):
            chunk = all_new_posts[chunk_idx * chunk_size : (chunk_idx + 1) * chunk_size]
            if not chunk:
                break
                
            elapsed_time = time.time() - start_time
            if elapsed_time > max_run_time_seconds:
                logger.warning("📡 CSI poll @%s: Time limit exceeded (%.1fs). Saving incremental state and exiting.", cfg.handle, elapsed_time)
                break

            enrichment_cache: dict[str, str] = {}
            
            # --- 1. Concurrent URL Enrichment ---
            if url_enrichment_enabled:
                try:
                    from universal_agent.services.csi_url_judge import enrich_urls, build_linked_context
                    enrich_dir = packet_dir / "url_enrichment"
                    
                    def enrich_post(post: dict[str, Any]) -> tuple[str, str | None]:
                        post_id = str(post.get("id") or "").strip()
                        post_links = extract_links(post)
                        if not post_links:
                            return post_id, None
                        try:
                            records = enrich_urls(
                                urls=post_links,
                                context=str(post.get("text") or "")[:2000],
                                output_dir=enrich_dir / post_id,
                                max_fetch=3,
                                timeout=15,
                            )
                            linked_ctx = build_linked_context(records)
                            if linked_ctx:
                                return post_id, linked_ctx
                        except Exception as exc:
                            logger.warning("CSI URL enrichment failed for post %s: %s", post_id, exc)
                        return post_id, None

                    with ThreadPoolExecutor(max_workers=5) as executor:
                        future_to_post = {executor.submit(enrich_post, p): p for p in chunk}
                        for future in as_completed(future_to_post):
                            pid, ctx = future.result()
                            if ctx:
                                enrichment_cache[pid] = ctx
                except ImportError:
                    logger.warning("csi_url_judge module not available; skipping URL enrichment")

            # --- 2. Concurrent Post Classification ---
            def classify_worker(post: dict[str, Any]) -> dict[str, Any]:
                return classify_post(
                    post,
                    handle=cfg.handle,
                    linked_context=enrichment_cache.get(str(post.get("id") or "").strip(), "")
                )

            chunk_actions = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(classify_worker, p): p for p in chunk}
                for future in as_completed(futures):
                    try:
                        chunk_actions.append(future.result())
                    except Exception as e:
                        logger.error("Classification failed: %s", e)
            
            # --- 3. Incremental Packet & Artifact Saving ---
            new_posts.extend(chunk)
            actions.extend(chunk_actions)
                
            write_reference_kb_update(
                packet_dir=packet_dir,
                handle=cfg.handle,
                actions=chunk_actions,
                posts=chunk,
                artifacts_root=cfg.artifacts_root,
            )
            _write_packet_files(
                packet_dir=packet_dir,
                generated_at=generated_at,
                handle=cfg.handle,
                user_payload=user_payload,
                posts_payload=posts_payload,
                new_posts=new_posts,
                actions=actions,
                error="",
            )
            if conn is not None:
                artifact_id = register_packet_artifact(conn, packet_dir=packet_dir, handle=cfg.handle, actions=actions, new_posts=new_posts)
                if cfg.queue_task_hub:
                    queued += queue_follow_up_tasks(conn, handle=cfg.handle, packet_dir=packet_dir, actions=chunk_actions)
                    
            # Checkpoint the state forward
            current_state = _next_state(
                state=current_state,
                handle=cfg.handle,
                user_id=str((user_payload.get("data") or {}).get("id") or ""),
                posts=chunk,
                generated_at=generated_at
            )
            _save_state(state_path, current_state)

        # ── Short-circuit: no new posts processed ──
        if not new_posts:
            # Still update state (tracks last_success_at, seen_post_ids)
            import shutil
            _save_state(
                state_path,
                _next_state(state=current_state, handle=cfg.handle, user_id=str((user_payload.get("data") or {}).get("id") or ""), posts=posts, generated_at=generated_at),
            )
            try:
                if packet_dir.exists() and not any(packet_dir.iterdir()):
                    shutil.rmtree(packet_dir, ignore_errors=True)
            except Exception:
                pass
            run.ok = True
            run.user_id = str((user_payload.get("data") or {}).get("id") or "")
            run.auth_mode = auth_mode
            run.new_post_count = 0
            run.seen_post_count = len(posts)
            run.action_count = 0
            run.queued_task_count = 0
            run.packet_dir = ""
            logger.info("📡 CSI poll @%s: no new posts (seen=%d). Skipping packet creation.", cfg.handle, len(posts))
            return run

        run.ok = True
        run.user_id = str((user_payload.get("data") or {}).get("id") or "")
        run.auth_mode = auth_mode
        run.new_post_count = len(new_posts)
        run.seen_post_count = len(posts)
        run.action_count = len(actions)
        run.queued_task_count = queued
        run.artifact_id = artifact_id
        run.actions = actions
        return run
    except Exception as exc:
        error = str(exc)
        write_reference_kb_update(
            packet_dir=packet_dir,
            handle=cfg.handle,
            actions=actions,
            posts=new_posts,
            artifacts_root=cfg.artifacts_root,
        )
        _write_packet_files(
            packet_dir=packet_dir,
            generated_at=generated_at,
            handle=cfg.handle,
            user_payload=user_payload,
            posts_payload=posts_payload,
            new_posts=new_posts,
            actions=actions,
            error=error,
        )
        run.error = error
        return run

def fetch_user_by_username(client: httpx.Client, *, token: str, username: str) -> dict[str, Any]:
    resp = client.get(
        f"https://api.x.com/2/users/by/username/{username}",
        headers=_auth_headers(token),
        params={"user.fields": "created_at,description,entities,public_metrics,verified,verified_type"},
    )
    return _json_response(resp)


def fetch_user_by_username_with_fallbacks(client: httpx.Client, *, token: str, username: str) -> dict[str, Any]:
    url = f"https://api.x.com/2/users/by/username/{username}"
    params = {"user.fields": "created_at,description,entities,public_metrics,verified,verified_type"}
    return _get_json_with_auth_fallbacks(client, url=url, params=params, app_bearer_token=token)


def fetch_user_posts(
    client: httpx.Client,
    *,
    token: str,
    user_id: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    since_id: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {
        "max_results": str(max(5, min(int(max_results or DEFAULT_MAX_RESULTS), 100))),
        "tweet.fields": "id,text,created_at,public_metrics,entities,conversation_id,referenced_tweets,attachments",
        "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
        "user.fields": "id,name,username,verified,public_metrics",
        "media.fields": "media_key,type,url,preview_image_url,alt_text",
        "exclude": "retweets",
    }
    if since_id:
        params["since_id"] = since_id
    resp = client.get(f"https://api.x.com/2/users/{user_id}/tweets", headers=_auth_headers(token), params=params)
    return _json_response(resp)


def fetch_user_posts_with_fallbacks(
    client: httpx.Client,
    *,
    token: str,
    user_id: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    since_id: str | None = None,
    pagination_token: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {
        "max_results": str(max(5, min(int(max_results or DEFAULT_MAX_RESULTS), 100))),
        "tweet.fields": "id,text,created_at,public_metrics,entities,conversation_id,referenced_tweets,attachments",
        "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
        "user.fields": "id,name,username,verified,public_metrics",
        "media.fields": "media_key,type,url,preview_image_url,alt_text",
        "exclude": "retweets",
    }
    if since_id:
        params["since_id"] = since_id
    if pagination_token:
        params["pagination_token"] = pagination_token
    return _get_json_with_auth_fallbacks(
        client,
        url=f"https://api.x.com/2/users/{user_id}/tweets",
        params=params,
        app_bearer_token=token,
    )


def fetch_all_user_posts_paginated(
    client: httpx.Client,
    *,
    token: str,
    user_id: str,
    max_results: int = 100,
    since_id: str | None = None,
    max_pages: int = 20,
    cutoff_days: int = 60,
) -> list[dict[str, Any]]:
    """Fetch all user posts with automatic pagination up to *cutoff_days* back.

    Returns a flat list of raw post dicts (newest-first).  The caller is
    responsible for deduplication.
    """
    from datetime import timedelta

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    all_posts: list[dict[str, Any]] = []
    next_token: str | None = None

    for page in range(1, max_pages + 1):
        payload = fetch_user_posts_with_fallbacks(
            client,
            token=token,
            user_id=user_id,
            max_results=max_results,
            since_id=since_id,
            pagination_token=next_token,
        )
        posts = normalize_posts(payload)
        if not posts:
            logger.info("Paginated fetch page %d: empty, stopping.", page)
            break

        reached_cutoff = False
        for post in posts:
            created = str(post.get("created_at") or "")
            if created:
                try:
                    post_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if post_dt < cutoff_dt:
                        reached_cutoff = True
                        break
                except (ValueError, TypeError):
                    pass
            all_posts.append(post)

        logger.info(
            "Paginated fetch page %d: %d posts (%s → %s)%s",
            page,
            len(posts),
            posts[0].get("created_at", "?"),
            posts[-1].get("created_at", "?"),
            " [cutoff reached]" if reached_cutoff else "",
        )

        if reached_cutoff:
            break

        meta = payload.get("meta", {})
        next_token = str(meta.get("next_token") or "").strip() or None
        if not next_token:
            break

        time.sleep(1)  # Rate-limit courtesy

    return all_posts


def normalize_posts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("id") or "").strip()
        if not post_id:
            continue
        out.append(dict(item))
    return out


def classify_post(post: dict[str, Any], *, handle: str = DEFAULT_HANDLE, linked_context: str = "") -> dict[str, Any]:
    post_id = str(post.get("id") or "").strip()
    text = str(post.get("text") or "").strip()
    lowered = text.lower()
    links = extract_links(post)
    heuristic = _heuristic_classification(text=text, lowered=lowered, links=links, linked_context=linked_context)
    llm_result = _llm_assisted_classification(
        text=text,
        links=links,
        heuristic=heuristic,
        linked_context=linked_context,
    )
    tier = int(llm_result.get("tier") or heuristic["tier"])
    action_type = str(llm_result.get("action_type") or heuristic["action_type"] or "digest").strip()
    if action_type not in {"digest", "kb_update", "demo_task", "strategic_follow_up"}:
        action_type = heuristic["action_type"]
    return {
        "post_id": post_id,
        "url": f"https://x.com/{handle.strip().lstrip('@') or DEFAULT_HANDLE}/status/{post_id}" if post_id else "",
        "created_at": str(post.get("created_at") or ""),
        "text": text,
        "tier": tier,
        "action_type": action_type,
        "links": links,
        "matched_terms": list(heuristic["matched_terms"]),
        "reasons": list(heuristic["reasons"]),
        "classifier": {
            "method": str(llm_result.get("method") or "heuristic"),
            "content_kind": str(llm_result.get("content_kind") or heuristic.get("content_kind") or "generic_update"),
            "confidence": str(llm_result.get("confidence") or heuristic.get("confidence") or "medium"),
            "reasoning": str(llm_result.get("reasoning") or ""),
            "heuristic_tier": int(heuristic["tier"]),
            "heuristic_action_type": str(heuristic["action_type"]),
        },
    }


def _heuristic_classification(*, text: str, lowered: str, links: list[str], linked_context: str = "") -> dict[str, Any]:
    matched_terms = sorted({term for term in _TIER2_TERMS | _TIER3_TERMS | _TIER4_TERMS if term in lowered})
    tier = 1
    reasons = ["informational Claude Code update"]
    content_kind = "generic_update"
    linked_lower = str(linked_context or "").lower()
    if links or _VERSION_RE.search(text) or any(term in lowered for term in _TIER2_TERMS):
        tier = 2
        reasons.append("reference material or version/update signal")
        content_kind = "docs_reference"
    if any(term in lowered for term in _TIER3_TERMS):
        tier = max(tier, 3)
        reasons.append("implementation or demo opportunity")
        content_kind = "product_capability"
    if any(term in lowered for term in _TIER4_TERMS):
        tier = max(tier, 4)
        reasons.append("migration, safety, or breakage risk")
        content_kind = "migration_risk"
    if any(term in lowered for term in _COMMUNITY_EVENT_TERMS):
        # Community/event posts are easy to over-rate; keep them reference-level
        # unless a stronger strategic signal is present.
        content_kind = "community_event"
        if tier == 3:
            tier = 2
            reasons.append("community/event announcement downshifted by deterministic fallback")
    if "source_type=event_page" in linked_lower and content_kind == "community_event" and tier > 1:
        tier = min(tier, 2)
        reasons.append("linked event-page context kept the post below demo_task")
    if any(token in linked_lower for token in ("source_type=github_repo", "source_type=github_file", "source_type=docs_page", "source_type=vendor_docs")) and tier < 3:
        tier = 3
        content_kind = "product_capability"
        reasons.append("linked source contains code/docs material that may justify implementation work")
    action_type = {
        1: "digest",
        2: "kb_update",
        3: "demo_task",
        4: "strategic_follow_up",
    }.get(tier, "digest")
    return {
        "tier": tier,
        "action_type": action_type,
        "matched_terms": matched_terms,
        "reasons": reasons,
        "content_kind": content_kind,
        "confidence": "medium",
    }


def _llm_assisted_classification(*, text: str, links: list[str], heuristic: dict[str, Any], linked_context: str = "") -> dict[str, Any]:
    if str(os.getenv("UA_CLAUDE_CODE_INTEL_LLM_CLASSIFIER_ENABLED", "1")).strip().lower() not in _TRUTHY:
        return {"method": "heuristic"}
    if not _has_llm_key():
        return {"method": "heuristic"}
    user = (
        "Classify this Claude Code intelligence post.\n\n"
        f"Post text:\n{text[:4000]}\n\n"
        f"Links:\n{json.dumps(links[:10], ensure_ascii=True)}\n\n"
        f"Linked source context:\n{linked_context[:4000] or '(none)'}\n\n"
        f"Heuristic suggestion:\n{json.dumps({k: heuristic[k] for k in ('tier', 'action_type', 'content_kind')}, ensure_ascii=True)}"
    )
    try:
        raw = _call_sync_llm(system=_TIER_CLASSIFIER_SYSTEM, user=user, max_tokens=300)
        parsed = _parse_json_object(raw)
        tier = int(parsed.get("tier") or 0)
        action_type = str(parsed.get("action_type") or "").strip()
        if tier not in {1, 2, 3, 4}:
            return {"method": "heuristic"}
        if action_type not in {"digest", "kb_update", "demo_task", "strategic_follow_up"}:
            return {"method": "heuristic"}
        return {
            "method": "llm",
            "tier": tier,
            "action_type": action_type,
            "content_kind": str(parsed.get("content_kind") or "").strip(),
            "confidence": str(parsed.get("confidence") or "medium").strip().lower(),
            "reasoning": str(parsed.get("reasoning") or "").strip(),
        }
    except Exception as exc:
        logger.warning("Claude Code Intel LLM classification failed; using heuristic fallback: %s", exc)
        return {"method": "heuristic", "reasoning": f"fallback: {type(exc).__name__}"}


def extract_links(post: dict[str, Any]) -> list[str]:
    links: list[str] = []
    entities = post.get("entities")
    if isinstance(entities, dict):
        for item in entities.get("urls") or []:
            if not isinstance(item, dict):
                continue
            for key in ("expanded_url", "unwound_url", "url"):
                value = str(item.get(key) or "").strip()
                if value:
                    links.append(value)
                    break
    links.extend(_URL_RE.findall(str(post.get("text") or "")))
    deduped: list[str] = []
    seen: set[str] = set()
    for link in links:
        clean = link.rstrip(".,)")
        # t.co shortlinks are redundant — the X API already provides expanded URLs
        # via entities.urls[].expanded_url / unwound_url.  Keeping them would cause
        # duplicate fetch attempts that 403 because t.co blocks non-browser UAs.
        if clean.startswith("https://t.co/") or clean.startswith("http://t.co/"):
            continue
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped


def register_packet_artifact(
    conn: sqlite3.Connection,
    *,
    packet_dir: Path,
    handle: str,
    actions: list[dict[str, Any]],
    new_posts: list[dict[str, Any]],
) -> str:
    max_tier = max([int(action.get("tier") or 0) for action in actions] or [0])
    status = ARTIFACT_STATUS_CANDIDATE if max_tier >= 2 else ARTIFACT_STATUS_PRODUCED
    title = f"Claude Code Intel packet: @{handle}"
    summary = (
        f"Captured {len(new_posts)} new @{handle} X posts; max tier {max_tier}."
        if new_posts
        else f"Captured an @{handle} X API poll with no new posts."
    )
    artifact = upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind=SOURCE_KIND_UPDATE,
            source_ref=str(packet_dir),
            artifact_type="claude_code_intel_packet",
            title=title,
        ),
        artifact_type="claude_code_intel_packet",
        source_kind=SOURCE_KIND_UPDATE,
        source_ref=str(packet_dir),
        title=title,
        summary=summary,
        status=status,
        priority=max(1, min(max_tier or 1, 4)),
        artifact_path=str(packet_dir / "digest.md"),
        topic_tags=["claude-code", "x-api", "claudedevs"],
        metadata={"packet_dir": str(packet_dir), "new_post_count": len(new_posts), "max_tier": max_tier},
    )
    return str(artifact.get("artifact_id") or "")


def queue_follow_up_tasks(
    conn: sqlite3.Connection,
    *,
    handle: str,
    packet_dir: Path,
    actions: list[dict[str, Any]],
) -> int:
    task_hub.ensure_schema(conn)
    queued = 0
    for action in actions:
        tier = int(action.get("tier") or 0)
        if tier < 3:
            continue
        post_id = str(action.get("post_id") or "").strip()
        if not post_id:
            continue
        source_kind = SOURCE_KIND_DEMO_TASK if tier == 3 else SOURCE_KIND_KB_UPDATE
        task_id = f"{source_kind}:{hashlib.sha256(post_id.encode()).hexdigest()[:16]}"
        title = (
            f"Build Claude Code demo from @{handle} update"
            if tier == 3
            else f"Analyze strategic Claude Code update from @{handle}"
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": source_kind,
                "source_ref": post_id,
                "title": title,
                "description": _task_description(handle=handle, packet_dir=packet_dir, action=action),
                "project_key": "proactive",
                "priority": max(1, min(tier, 4)),
                "labels": ["agent-ready", "claude-code-intel", "x-api", "codie" if tier == 3 else "atlas"],
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "trigger_type": "heartbeat_poll",
                "metadata": {
                    "source": LANE_SLUG,
                    "post_id": post_id,
                    "post_url": action.get("url") or "",
                    "packet_dir": str(packet_dir),
                    "tier": tier,
                    "action_type": action.get("action_type") or "",
                    "links": action.get("links") or [],
                    "preferred_vp": "vp.coder.primary" if tier == 3 else "vp.general.primary",
                    "knowledge_base_slug": KB_SLUG,
                    "workflow_manifest": {
                        "workflow_kind": "code_change" if tier == 3 else "research",
                        "delivery_mode": "interactive_chat",
                        "requires_pdf": False,
                        "final_channel": "chat",
                        "canonical_executor": "simone_first",
                        "repo_mutation_allowed": tier == 3,
                    },
                },
            },
        )
        upsert_artifact(
            conn,
            artifact_type="claude_code_follow_up_task",
            source_kind=source_kind,
            source_ref=post_id,
            title=title,
            summary=f"Queued Tier {tier} Claude Code intelligence follow-up for @{handle} post {post_id}.",
            status=ARTIFACT_STATUS_CANDIDATE,
            priority=max(1, min(tier, 4)),
            source_url=str(action.get("url") or ""),
            artifact_path=str(packet_dir / "digest.md"),
            topic_tags=["claude-code", "x-api", "task-hub"],
            metadata={"task_id": task_id, "packet_dir": str(packet_dir), "tier": tier},
        )
        queued += 1
    return queued


# ── Activity-event emission for CSI ──────────────────────────────────────
#
# The activity_events table is what the Notifications & Events dashboard reads.
# The CSI pipeline previously only wrote to proactive_artifacts and task_hub,
# leaving "csi" events invisible in the dashboard.

_ACTIVITY_SCHEMA_ENSURED = False

def _ensure_activity_events_table(conn: sqlite3.Connection) -> None:
    """Idempotently create the activity_events table (subset of gateway schema)."""
    global _ACTIVITY_SCHEMA_ENSURED
    if _ACTIVITY_SCHEMA_ENSURED:
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY,
            event_class TEXT NOT NULL DEFAULT 'notification',
            source_domain TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            full_message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'new',
            requires_action INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_ref_json TEXT NOT NULL DEFAULT '{}',
            actions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            channels_json TEXT NOT NULL DEFAULT '[]',
            email_targets_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_activity_events_created_at ON activity_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_source_domain ON activity_events(source_domain, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_kind ON activity_events(kind, created_at DESC);
    """)
    _ACTIVITY_SCHEMA_ENSURED = True


def emit_csi_activity_event(
    conn: sqlite3.Connection,
    *,
    handle: str,
    new_post_count: int,
    action_count: int,
    queued_task_count: int,
    packet_dir: str = "",
    actions: list[dict[str, Any]] | None = None,
    error: str = "",
) -> str:
    """Write a CSI sync-completed event to activity_events for dashboard visibility.

    Returns the generated event ID.
    """
    import uuid as _uuid

    _ensure_activity_events_table(conn)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_id = f"csi_sync_{handle}_{_uuid.uuid4().hex[:12]}"
    kind = "csi_sync_completed" if not error else "csi_sync_failed"
    severity = "info" if not error else "error"

    # Build a descriptive summary for the dashboard card
    tier_counts: dict[int, int] = {}
    for act in (actions or []):
        t = int(act.get("tier") or 0)
        if t > 0:
            tier_counts[t] = tier_counts.get(t, 0) + 1
    tier_summary = ", ".join(f"T{t}={c}" for t, c in sorted(tier_counts.items())) if tier_counts else "no tiers"

    title = f"CSI @{handle}: {new_post_count} new post{'s' if new_post_count != 1 else ''}"
    summary = (
        f"Polled @{handle}: {new_post_count} new, "
        f"{action_count} actions ({tier_summary}), "
        f"{queued_task_count} tasks queued."
    )
    if error:
        title = f"CSI @{handle}: sync failed"
        summary = f"Error syncing @{handle}: {error[:200]}"

    full_message = summary
    if packet_dir:
        full_message += f"\nPacket: {packet_dir}"

    metadata = {
        "handle": handle,
        "new_post_count": new_post_count,
        "action_count": action_count,
        "queued_task_count": queued_task_count,
        "tier_counts": tier_counts,
        "packet_dir": packet_dir,
        "pipeline": "csi_x_sync",
    }
    if error:
        metadata["error"] = error[:500]

    entity_ref = {
        "type": "csi_handle",
        "handle": handle,
        "dashboard_path": "/dashboard/claude-code-intel",
    }

    actions_json_list = []
    if packet_dir:
        actions_json_list.append({
            "id": "view_csi",
            "label": "View in CSI",
            "type": "navigate",
            "href": "/dashboard/claude-code-intel",
        })

    try:
        conn.execute(
            """
            INSERT INTO activity_events (
                id, event_class, source_domain, kind, title, summary, full_message,
                severity, status, requires_action, session_id,
                created_at, updated_at,
                entity_ref_json, actions_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                summary=excluded.summary,
                full_message=excluded.full_message,
                severity=excluded.severity,
                status=excluded.status,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                event_id,
                "notification",
                "csi",           # source_domain — matches the routing table
                kind,
                title,
                summary,
                full_message,
                severity,
                "new",
                0,               # requires_action
                "cron_claude_code_intel_sync",
                now_iso,
                now_iso,
                json.dumps(entity_ref),
                json.dumps(actions_json_list),
                json.dumps(metadata),
            ),
        )
        logger.info("📣 Emitted CSI activity event: %s — %s", event_id, summary)
    except Exception as exc:
        logger.warning("Failed to emit CSI activity event: %s", exc)

    return event_id


def write_reference_kb_update(
    *,
    packet_dir: Path,
    handle: str,
    actions: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    artifacts_root: Path | None = None,
) -> Path:
    kb_root = resolve_reference_kb_root(artifacts_root)
    kb_root.mkdir(parents=True, exist_ok=True)
    index_path = kb_root / "source_index.md"
    lines = [
        "# Claude Code Intelligence Source Index",
        "",
        "This local reference index is maintained by the Claude Code X intelligence lane.",
        "Canonical design and implementation docs live under `docs/`.",
        "",
        f"- Latest packet: `{packet_dir}`",
        f"- Source handle: `@{handle}`",
        f"- New posts in latest packet: `{len(posts)}`",
        "",
        "## Latest Actions",
        "",
    ]
    if not actions:
        lines.append("- No new action-worthy posts in the latest packet.")
    for action in actions:
        lines.append(
            f"- Tier {action.get('tier')}: [{action.get('post_id')}]({action.get('url')}) "
            f"`{action.get('action_type')}`"
        )
        for link in action.get("links") or []:
            lines.append(f"  - {link}")
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return index_path


def _task_description(*, handle: str, packet_dir: Path, action: dict[str, Any]) -> str:
    links = "\n".join(f"- {link}" for link in action.get("links") or []) or "- None found in the post payload."
    return "\n".join(
        [
            f"Process a Tier {action.get('tier')} Claude Code intelligence item from X account @{handle}.",
            "",
            f"Post URL: {action.get('url') or '(missing)'}",
            f"Packet: {packet_dir}",
            "",
            "Post text:",
            str(action.get("text") or "").strip(),
            "",
            "Referenced links:",
            links,
            "",
            "Expected output:",
            "- Read the packet files before acting.",
            "- Study referenced docs/articles if links are present.",
            "- Update the Claude Code intelligence knowledge base notes when durable knowledge is learned.",
            "- For Tier 3 items, build a small private demo or implementation plan if the capability is code-worthy.",
            "  - IMPORTANT: When building code or demos on the VPS, ALWAYS save them to `/home/ua/vpsrepos/<project_name>` instead of the ephemeral run workspace.",
            "- Do not post to X. This lane is read-only unless explicitly re-authorized.",
        ]
    )


def _write_packet_files(
    *,
    packet_dir: Path,
    generated_at: str,
    handle: str,
    user_payload: dict[str, Any],
    posts_payload: dict[str, Any],
    new_posts: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    error: str,
) -> None:
    _write_json(packet_dir / "raw_user.json", user_payload)
    _write_json(packet_dir / "raw_posts.json", posts_payload)
    _write_json(packet_dir / "new_posts.json", new_posts)
    _write_json(packet_dir / "actions.json", actions)
    (packet_dir / "source_links.md").write_text(_source_links_markdown(actions), encoding="utf-8")
    (packet_dir / "triage.md").write_text(_triage_markdown(actions=actions, error=error), encoding="utf-8")
    (packet_dir / "digest.md").write_text(_digest_markdown(handle=handle, actions=actions, new_posts=new_posts, error=error), encoding="utf-8")
    manifest = {
        "lane": LANE_SLUG,
        "handle": handle,
        "generated_at": generated_at,
        "ok": not bool(error),
        "error": error,
        "new_post_count": len(new_posts),
        "action_count": len(actions),
        "files": [
            "raw_user.json",
            "raw_posts.json",
            "new_posts.json",
            "source_links.md",
            "triage.md",
            "actions.json",
            "digest.md",
        ],
    }
    _write_json(packet_dir / "manifest.json", manifest)


def _digest_markdown(*, handle: str, actions: list[dict[str, Any]], new_posts: list[dict[str, Any]], error: str) -> str:
    lines = [f"# Claude Code Intel Digest: @{handle}", ""]
    if error:
        lines.extend(["## Status", "", f"Source failure: `{error}`", ""])
        return "\n".join(lines).rstrip() + "\n"
    lines.extend(["## Summary", "", f"- New posts: `{len(new_posts)}`", f"- Actions: `{len(actions)}`", ""])
    if not actions:
        lines.append("No new posts were found in this poll.")
    for action in actions:
        lines.extend(
            [
                f"## Tier {action.get('tier')} - {action.get('action_type')}",
                "",
                f"- Post: {action.get('url') or action.get('post_id')}",
                f"- Reasons: {', '.join(action.get('reasons') or [])}",
                "",
                str(action.get("text") or "").strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _source_links_markdown(actions: list[dict[str, Any]]) -> str:
    lines = ["# Source Links", ""]
    found = False
    for action in actions:
        for link in action.get("links") or []:
            lines.append(f"- {link}")
            found = True
    if not found:
        lines.append("- No external links found.")
    return "\n".join(lines).rstrip() + "\n"


def _triage_markdown(*, actions: list[dict[str, Any]], error: str) -> str:
    lines = ["# Triage", ""]
    if error:
        lines.append(f"- Source failure: `{error}`")
        return "\n".join(lines).rstrip() + "\n"
    if not actions:
        lines.append("- Tier 0: no new posts.")
    for action in actions:
        lines.extend(
            [
                f"- Tier {action.get('tier')}: `{action.get('action_type')}` for post `{action.get('post_id')}`",
                f"  - Matched terms: {', '.join(action.get('matched_terms') or []) or '(none)'}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": "universal-agent-claude-code-intel/1.0"}


def _has_llm_key() -> bool:
    return bool(
        str(
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ZAI_API_KEY")
            or ""
        ).strip()
    )


def _call_sync_llm(*, system: str, user: str, max_tokens: int = 300) -> str:
    from anthropic import Anthropic

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("No Anthropic-compatible API key available")
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    client = Anthropic(**client_kwargs)
    response = client.messages.create(
        model=resolve_sonnet(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    chunks: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            chunks.append(block.text)
    return "".join(chunks).strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    parsed = json.loads(cleaned)
    return parsed if isinstance(parsed, dict) else {}


def _get_json_with_auth_fallbacks(
    client: httpx.Client,
    *,
    url: str,
    params: dict[str, str],
    app_bearer_token: str,
) -> dict[str, Any]:
    candidates: list[tuple[str, dict[str, str]]] = []
    if str(app_bearer_token or "").strip():
        candidates.append(("app_bearer", _auth_headers(app_bearer_token)))
    oauth2_token = str(os.getenv("X_OAUTH2_ACCESS_TOKEN") or "").strip()
    if oauth2_token:
        candidates.append(("oauth2_user", _auth_headers(oauth2_token)))
    oauth1_headers = _oauth1_headers("GET", url, params=params)
    if oauth1_headers:
        candidates.append(("oauth1_user", oauth1_headers))

    if not candidates:
        raise RuntimeError("missing X auth credentials")

    auth_failures: list[str] = []
    last_payload: dict[str, Any] = {}
    for auth_mode, headers in candidates:
        resp = client.get(url, headers=headers, params=params)
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        if resp.status_code < 400:
            out = payload if isinstance(payload, dict) else {}
            out["_ua_auth_mode"] = auth_mode
            return out
        last_payload = payload if isinstance(payload, dict) else {}
        detail = _x_error_detail(last_payload)
        auth_failures.append(f"{auth_mode}: HTTP {resp.status_code}{(': ' + detail) if detail else ''}")
        if resp.status_code not in {401, 403}:
            raise RuntimeError(f"X API request failed: HTTP {resp.status_code}{(': ' + detail) if detail else ''}")

    raise RuntimeError("X API auth attempts failed: " + "; ".join(auth_failures))


def _has_user_auth_fallback() -> bool:
    if str(os.getenv("X_OAUTH2_ACCESS_TOKEN") or "").strip():
        return True
    return all(
        str(os.getenv(key) or "").strip()
        for key in (
            "X_OAUTH_CONSUMER_KEY",
            "X_OAUTH_CONSUMER_SECRET",
            "X_OAUTH_ACCESS_TOKEN",
            "X_OAUTH_ACCESS_TOKEN_SECRET",
        )
    )


def _oauth1_headers(method: str, url: str, *, params: dict[str, str] | None = None) -> dict[str, str] | None:
    consumer_key = str(os.getenv("X_OAUTH_CONSUMER_KEY") or "").strip()
    consumer_secret = str(os.getenv("X_OAUTH_CONSUMER_SECRET") or "").strip()
    access_token = str(os.getenv("X_OAUTH_ACCESS_TOKEN") or "").strip()
    access_token_secret = str(os.getenv("X_OAUTH_ACCESS_TOKEN_SECRET") or "").strip()
    if not all((consumer_key, consumer_secret, access_token, access_token_secret)):
        return None

    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": hashlib.sha256(f"{time.time_ns()}:{os.getpid()}".encode()).hexdigest()[:32],
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    split = urlsplit(url)
    base_url = f"{split.scheme}://{split.netloc}{split.path}"
    signature_params: dict[str, str] = {}
    signature_params.update(params or {})
    signature_params.update(oauth_params)
    encoded_pairs = [
        (quote(str(key), safe=""), quote(str(value), safe=""))
        for key, value in signature_params.items()
    ]
    parameter_string = "&".join(f"{key}={value}" for key, value in sorted(encoded_pairs))
    signature_base = "&".join(
        [
            method.upper(),
            quote(base_url, safe=""),
            quote(parameter_string, safe=""),
        ]
    )
    signing_key = f"{quote(consumer_secret, safe='')}&{quote(access_token_secret, safe='')}"
    digest = hmac.new(signing_key.encode(), signature_base.encode(), "sha1").digest()
    import base64

    oauth_params["oauth_signature"] = base64.b64encode(digest).decode()
    header_value = "OAuth " + ", ".join(
        f'{quote(key, safe="")}="{quote(value, safe="")}"'
        for key, value in sorted(oauth_params.items())
    )
    return {"Authorization": header_value, "User-Agent": "universal-agent-claude-code-intel/1.0"}


def _json_response(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if resp.status_code >= 400:
        detail = _x_error_detail(payload if isinstance(payload, dict) else {})
        raise RuntimeError(f"X API request failed: HTTP {resp.status_code}{(': ' + detail) if detail else ''}")
    return payload if isinstance(payload, dict) else {}


def _x_error_detail(payload: dict[str, Any]) -> str:
    return str(payload.get("title") or payload.get("detail") or payload.get("errors") or payload.get("error") or "").strip()


def _new_packet_dir(root: Path, *, handle: str, generated_at: str) -> Path:
    dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    date = dt.strftime("%Y-%m-%d")
    stamp = dt.strftime("%H%M%S")
    safe_handle = re.sub(r"[^A-Za-z0-9_.-]+", "-", handle.strip().lstrip("@")) or DEFAULT_HANDLE
    return root / "packets" / date / f"{stamp}__{safe_handle}"


def resolve_state_path(lane_root: Path, handle: str) -> Path:
    """Per-handle state file. Falls back to legacy state.json for migration."""
    safe_handle = str(handle or DEFAULT_HANDLE).strip().lstrip("@").lower() or DEFAULT_HANDLE.lower()
    per_handle = lane_root / f"state__{safe_handle}.json"
    if per_handle.exists():
        return per_handle
    # Migrate from legacy state.json if it matches this handle
    legacy = lane_root / "state.json"
    if legacy.exists():
        try:
            legacy_state = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            legacy_state = {}
        legacy_handle = str(legacy_state.get("handle") or "").strip().lstrip("@").lower()
        if legacy_handle == safe_handle or not legacy_handle:
            # Copy legacy state into per-handle file on first access
            _save_state(per_handle, legacy_state)
            return per_handle
    return per_handle


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_post_ids": []}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_post_ids": []}
    return parsed if isinstance(parsed, dict) else {"seen_post_ids": []}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, state)


def _next_state(*, state: dict[str, Any], handle: str, user_id: str, posts: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    existing = [str(v) for v in state.get("seen_post_ids", []) if str(v)]
    ids = [str(post.get("id") or "").strip() for post in posts if str(post.get("id") or "").strip()]
    merged = list(dict.fromkeys(ids + existing))[:5000]
    next_state = dict(state)
    next_state.update(
        {
            "handle": handle,
            "user_id": user_id,
            "last_success_at": generated_at,
            "last_seen_post_id": ids[0] if ids else str(state.get("last_seen_post_id") or ""),
            "seen_post_ids": merged,
        }
    )
    return next_state


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_int(raw: Any, default: int, *, low: int, high: int) -> int:
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(low, min(value, high))
