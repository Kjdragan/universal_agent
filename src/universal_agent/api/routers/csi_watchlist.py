import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/csi/watchlist", tags=["csi", "watchlist"])

# Defaults exactly like csi_bridge, but mapped safely
_DEFAULT_WATCHLIST_FILE = "/var/lib/universal-agent/csi/channels_watchlist.json"

# Fallback for classification if we can't import
# we will try to import source_manager
try:
    import sys
    # Add CSI_Ingester/development to path if needed to load source_manager
    csi_dev_path = Path("/opt/universal_agent/CSI_Ingester/development")
    if csi_dev_path.exists() and str(csi_dev_path) not in sys.path:
        sys.path.append(str(csi_dev_path))
    from csi_ingester.store.source_manager import _classify_channel_name
except ImportError:
    logger.warning("Could not import _classify_channel_name from CSI_Ingester. Using fallback classification.")
    def _classify_channel_name(name: str) -> str:
        name_lower = name.lower()
        if any(x in name_lower for x in ["ai", "gpt", "llm", "claude"]): return "ai_models"
        if any(x in name_lower for x in ["code", "dev", "python"]): return "ai_coding"
        if any(x in name_lower for x in ["war", "military", "conflict"]): return "conflict"
        if any(x in name_lower for x in ["tech", "linux"]): return "technology"
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
                ch["domain"] = _classify_channel_name(ch.get("channel_name", ""))
                
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
            
        new_channel = {
            "channel_id": channel_id,
            "channel_name": channel_title,
            "video_count": 1,
            "rss_feed_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            "youtube_url": f"https://www.youtube.com/channel/{channel_id}"
        }
        
        # Insert at top or bottom? Default structure adds at bottom
        channels.append(new_channel)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        new_channel["domain"] = _classify_channel_name(channel_title)
            
        return {"success": True, "message": "Channel added", "channel": new_channel}
        
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

