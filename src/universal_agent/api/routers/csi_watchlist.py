import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/csi/watchlist", tags=["csi", "watchlist"])

# Defaults exactly like csi_bridge, but mapped safely
_DEFAULT_WATCHLIST_FILE = "/var/lib/universal-agent/csi/channels_watchlist.json"

# ── 10-Category LLM Channel Classifier ──────────────────────────────────────

_VALID_CATEGORIES = [
    "ai_coding_and_agents",
    "ai_models_and_research",
    "ai_news_and_business",
    "software_engineering",
    "geopolitics_and_conflict",
    "longform_interviews",
    "cooking",
    "personal_health",
    "other_signal",
    "noise",
]

_CLASSIFY_SYSTEM = """You are a taxonomy classifier for YouTube channels. Categorize the channel into EXACTLY ONE of the following categories:
1. ai_coding_and_agents — Channels focused on coding with AI, AI agents, AI dev tools
2. ai_models_and_research — Channels covering AI models, papers, ML research
3. ai_news_and_business — AI industry news, business strategy, AI startups
4. software_engineering — General software dev, web dev, DevOps, system design
5. geopolitics_and_conflict — Military analysis, geopolitics, conflict reporting
6. longform_interviews — Interview/podcast-style shows with long-form discussions
7. cooking — Cooking, recipes, food content
8. personal_health — Fitness, health, wellness, biohacking
9. other_signal — Valuable content that doesn't fit the above categories
10. noise — Low-value, clickbait, or irrelevant content

Respond with ONLY a JSON object: {"category": "<exact_category_string>"}"""


async def _classify_channel_llm(
    channel_name: str,
    description: str = "",
    transcript_samples: list[str] | None = None,
) -> tuple[str, str]:
    """Classify a channel using LLM. Returns (category, method)."""
    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,
            _parse_json_response,
        )

        content_parts = [f"Channel name: {channel_name}"]
        if description:
            content_parts.append(f"Channel description: {description[:500]}")
        method = "metadata"

        if transcript_samples:
            content_parts.append("Recent video content samples:")
            for sample in transcript_samples[:5]:
                content_parts.append(f"- {sample[:300]}")
            method = "transcript"

        raw = await _call_llm(
            system=_CLASSIFY_SYSTEM,
            user="\n".join(content_parts),
            max_tokens=100,
        )
        result = _parse_json_response(raw)
        category = result.get("category", "other_signal")

        if category not in _VALID_CATEGORIES:
            category = "other_signal"

        return category, method

    except Exception as e:
        logger.warning("LLM channel classification failed, using keyword fallback: %s", e)
        return _classify_channel_keyword(channel_name), "keyword_fallback"


def _classify_channel_keyword(name: str) -> str:
    """Fast keyword fallback when LLM is unavailable."""
    name_lower = name.lower()
    if any(x in name_lower for x in ["agent", "cursor", "copilot", "code", "dev", "python", "coding"]):
        return "ai_coding_and_agents"
    if any(x in name_lower for x in ["ai", "gpt", "llm", "claude", "machine learning", "neural"]):
        return "ai_models_and_research"
    if any(x in name_lower for x in ["war", "military", "conflict", "geopolit"]):
        return "geopolitics_and_conflict"
    if any(x in name_lower for x in ["cook", "recipe", "chef", "food", "kitchen"]):
        return "cooking"
    if any(x in name_lower for x in ["health", "fitness", "workout", "diet"]):
        return "personal_health"
    return "other_signal"


class AddChannelRequest(BaseModel):
    url: str

class Channel(BaseModel):
    channel_id: str
    channel_name: str
    video_count: int = 1
    rss_feed_url: str
    youtube_url: str
    domain: Optional[str] = None


def get_watchlist_path() -> Path:
    """Get the active watchlist file path."""
    return Path(os.getenv("CSI_YOUTUBE_WATCHLIST_FILE", _DEFAULT_WATCHLIST_FILE)).expanduser()


def _ensure_file_exists(file_path: Path):
    if not file_path.exists():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            json.dump({"channels": []}, f, indent=2)


@router.get("")
async def get_watchlist():
    """Retrieve the current list of YouTube channels."""
    path = get_watchlist_path()
    if not path.exists():
        # Fallback to local repo version if missing (development safety)
        local_fallback = Path("CSI_Ingester/development/channels_watchlist.json")
        if local_fallback.exists():
            path = local_fallback
        else:
            return {"channels": []}
            
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        channels = data.get("channels", [])
        categories = data.get("categories", [])
        
        # Enrich with classification if missing
        for ch in channels:
            if not ch.get("domain"):
                ch["domain"] = _classify_channel_keyword(ch.get("channel_name", ""))
                
        return {"channels": channels, "categories": categories}
    except Exception as e:
        logger.error(f"Error reading watchlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _purge_channel_from_db(channel_id: str):
    db_path = Path(os.getenv("CSI_DB_PATH", "/var/lib/universal-agent/csi/csi.db")).expanduser()
    if not db_path.exists():
        return
    try:
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM rss_event_analysis WHERE channel_id = ?", (channel_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Error purging DB for channel {channel_id}: {e}")

@router.delete("/{channel_id}")
async def delete_channel(channel_id: str):
    """Remove a channel from the watchlist by ID."""
    path = get_watchlist_path()
    _ensure_file_exists(path)
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        initial_count = len(data.get("channels", []))
        data["channels"] = [ch for ch in data.get("channels", []) if ch.get("channel_id") != channel_id]
        
        if len(data["channels"]) == initial_count:
            raise HTTPException(status_code=404, detail="Channel not found in watchlist.")
            
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        _purge_channel_from_db(channel_id)
            
        return {"success": True, "message": f"Channel {channel_id} removed", "channels": data["channels"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing from watchlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add")
async def add_channel(request: AddChannelRequest):
    """Add a new channel by extracting channel id via youtube API or scraping."""
    url = request.url.strip()
    
    # 1. First, let's see if it's already a channel URL
    channel_id = None
    if "?channel_id=" in url:
        channel_id = url.split("?channel_id=")[-1].split("&")[0]
    elif "/channel/" in url:
        channel_id = url.split("/channel/")[-1].split("/")[0].split("?")[0]
        
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    
    # If we still don't have a channel ID, we likely have a video URL or a handle URL (@username)
    if not channel_id:
        if not api_key:
            raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY must be configured to resolve video URLs")
            
        async with httpx.AsyncClient() as client:
            if "youtube.com/watch?v=" in url or "youtu.be/" in url:
                # Resolve video URL
                if "youtu.be/" in url:
                    video_id = url.split("youtu.be/")[-1].split("?")[0]
                else:
                    video_id = url.split("v=")[-1].split("&")[0]
                    
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "snippet", "id": video_id, "key": api_key}
                )
                if resp.status_code != 200 or not resp.json().get("items"):
                    raise HTTPException(status_code=400, detail="Could not resolve video metadata from YouTube API")
                item = resp.json()["items"][0]
                channel_id = item["snippet"]["channelId"]
                channel_title = item["snippet"]["channelTitle"]
            else:
                # Could be a handle link e.g. youtube.com/@username
                # Use search API
                handle = url.split("/")[-1].replace("@", "")
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "type": "channel", "q": handle, "key": api_key, "maxResults": 1}
                )
                if resp.status_code != 200 or not resp.json().get("items"):
                    raise HTTPException(status_code=400, detail="Could not resolve handle to a YouTube channel")
                item = resp.json()["items"][0]
                channel_id = item["snippet"]["channelId"]
                channel_title = item["snippet"]["channelTitle"]

    # At this point, we have channel_id. Let's explicitly fetch its name if we only matched by pattern earlier
    if channel_id and "channel_title" not in locals():
        if not api_key:
            channel_title = "Unknown Channel (API Key Missing)"
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "snippet", "id": channel_id, "key": api_key}
                )
                if resp.status_code == 200 and resp.json().get("items"):
                    channel_title = resp.json()["items"][0]["snippet"]["title"]
                else:
                    channel_title = f"Channel {channel_id}"

    if not channel_id:
        raise HTTPException(status_code=400, detail="Failed to extract Channel ID from URL")

    # Add to file
    path = get_watchlist_path()
    if not path.exists():
        # Maybe use fallback just to copy the structure initially
        local_fallback = Path("CSI_Ingester/development/channels_watchlist.json")
        if local_fallback.exists():
            with open(local_fallback, "r") as f:
             data = json.load(f)
             # copy to path
             path.parent.mkdir(parents=True, exist_ok=True)
             with open(path, "w") as fw:
                 json.dump(data, fw)
        else:
            _ensure_file_exists(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        channels = data.setdefault("channels", [])
        
        # Avoid duplicates
        if any(ch.get("channel_id") == channel_id for ch in channels):
            raise HTTPException(status_code=409, detail="Channel already in watchlist")
            
        # Fetch 3 recent video titles for better classification signal
        recent_titles: list[str] = []
        if api_key:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/search",
                        params={
                            "part": "snippet",
                            "channelId": channel_id,
                            "order": "date",
                            "type": "video",
                            "maxResults": 3,
                            "key": api_key,
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        for item in resp.json().get("items", []):
                            t = item.get("snippet", {}).get("title", "")
                            if t:
                                recent_titles.append(t)
            except Exception as exc:
                logger.debug("Could not fetch recent videos for classification: %s", exc)

        # Classify via LLM using channel name + recent video titles
        category, classify_method = await _classify_channel_llm(
            channel_title,
            description="",
            transcript_samples=recent_titles or None,
        )
        if recent_titles:
            classify_method = "recent_titles"
        
        new_channel = {
            "channel_id": channel_id,
            "channel_name": channel_title,
            "video_count": 1,
            "rss_feed_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            "youtube_url": f"https://www.youtube.com/channel/{channel_id}",
            "domain": category,
            "_categorization_method": classify_method,
        }
        
        channels.append(new_channel)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "message": f"Channel added ({category} via {classify_method})", "channel": new_channel}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding to watchlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CategoryRequest(BaseModel):
    name: str

class ChannelPatchRequest(BaseModel):
    domain: str

@router.post("/categories")
async def add_category(request: CategoryRequest):
    """Add a new category."""
    path = get_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        categories = data.setdefault("categories", [])
        if request.name in categories:
            raise HTTPException(status_code=409, detail="Category already exists")
            
        categories.append(request.name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "categories": categories}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding category: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/categories/{old_name}")
async def rename_category(old_name: str, request: CategoryRequest):
    """Rename a category and migrate channels."""
    path = get_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        categories = data.get("categories", [])
        if old_name in categories:
            categories[categories.index(old_name)] = request.name
        else:
            categories.append(request.name)
        data["categories"] = categories
            
        channels = data.get("channels", [])
        for ch in channels:
            if ch.get("domain") == old_name:
                ch["domain"] = request.name
                
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "categories": data["categories"], "channels": data["channels"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error renaming category: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/categories/{name}")
async def delete_category(name: str):
    """Delete a category and cascade delete channels including in DB."""
    path = get_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if "categories" in data and name in data["categories"]:
            data["categories"].remove(name)
            
        channels = data.get("channels", [])
        channels_to_delete = [ch for ch in channels if ch.get("domain") == name]
        data["channels"] = [ch for ch in channels if ch.get("domain") != name]
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        for ch in channels_to_delete:
            _purge_channel_from_db(ch.get("channel_id"))
            
        return {"success": True, "categories": data.get("categories", []), "channels": data["channels"]}
    except Exception as e:
        logger.error(f"Error deleting category: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{channel_id}")
async def patch_channel(channel_id: str, request: ChannelPatchRequest):
    """Update a specific channel's metadata (e.g. category reassignment)."""
    path = get_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        channels = data.get("channels", [])
        updated = False
        for ch in channels:
            if ch.get("channel_id") == channel_id:
                ch["domain"] = request.domain
                ch["_categorization_method"] = "manual"
                updated = True
                break
                
        if not updated:
            raise HTTPException(status_code=404, detail="Channel not found")
            
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "channels": data["channels"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error patching channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ReclassifyRequest(BaseModel):
    channel_id: str
    transcript_samples: list[str] = []


@router.post("/reclassify")
async def reclassify_channel(request: ReclassifyRequest):
    """Re-classify a channel using transcript samples (called when transcripts arrive)."""
    path = get_watchlist_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Watchlist not found")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        channel = None
        for ch in data.get("channels", []):
            if ch.get("channel_id") == request.channel_id:
                channel = ch
                break

        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        old_domain = channel.get("domain", "other_signal")
        old_method = channel.get("_categorization_method", "unknown")

        # Skip re-classification for manually assigned categories
        if old_method == "manual":
            return {
                "success": True,
                "skipped": True,
                "reason": "Channel was manually categorized",
                "domain": old_domain,
            }

        new_category, method = await _classify_channel_llm(
            channel.get("channel_name", ""),
            description="",
            transcript_samples=request.transcript_samples or None,
        )

        changed = new_category != old_domain
        channel["domain"] = new_category
        channel["_categorization_method"] = method

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(
                "Re-classified channel %s: %s -> %s (via %s)",
                channel.get("channel_name"),
                old_domain,
                new_category,
                method,
            )

        return {
            "success": True,
            "changed": changed,
            "old_domain": old_domain,
            "new_domain": new_category,
            "method": method,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reclassifying channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent-videos")
async def get_recent_videos(limit: int = 50):
    """Return the most recently ingested YouTube videos from the CSI events table."""
    import sqlite3

    db_path = Path(os.getenv("CSI_DB_PATH", "/var/lib/universal-agent/csi/csi.db")).expanduser()
    if not db_path.exists():
        return {"videos": []}

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT event_id, subject_json, created_at
            FROM events
            WHERE source = 'youtube_channel_rss'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (min(limit, 200),),
        ).fetchall()
        conn.close()

        videos = []
        for row in rows:
            try:
                subject = json.loads(row["subject_json"])
            except Exception:
                continue

            # Extract YouTube video ID from event_id like "yt:rss:<video_id>:<ts>"
            eid = row["event_id"] or ""
            parts = eid.split(":")
            video_id = parts[2] if len(parts) >= 3 else ""

            # Ensure UTC timestamps carry a 'Z' suffix so browsers
            # convert them to the viewer's local timezone (e.g. Houston CDT).
            # SQLite datetime('now') produces UTC strings without a tz marker,
            # which JS Date() would otherwise interpret as local time.
            raw_ingested = row["created_at"] or ""
            if raw_ingested and not raw_ingested.endswith(("Z", "+00:00", "+0000")) and "T" not in raw_ingested:
                # sqlite format: "2026-04-26 03:50:00" → "2026-04-26T03:50:00Z"
                raw_ingested = raw_ingested.replace(" ", "T") + "Z"
            elif raw_ingested and not raw_ingested.endswith(("Z", "+00:00", "+0000")):
                raw_ingested = raw_ingested + "Z"

            raw_published = subject.get("published_at") or subject.get("occurred_at") or ""
            if raw_published and not raw_published.endswith(("Z", "+00:00", "+0000")) and "T" not in raw_published:
                raw_published = raw_published.replace(" ", "T") + "Z"
            elif raw_published and not raw_published.endswith(("Z", "+00:00", "+0000")):
                raw_published = raw_published + "Z"

            videos.append({
                "video_id": video_id,
                "title": subject.get("title") or subject.get("description", "")[:80] or "Untitled",
                "channel_name": subject.get("channel_name") or subject.get("author_name") or "Unknown",
                "channel_id": subject.get("channel_id") or "",
                "published_at": raw_published,
                "ingested_at": raw_ingested,
            })

        return {"videos": videos}
    except Exception as e:
        logger.error("Error fetching recent videos: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
