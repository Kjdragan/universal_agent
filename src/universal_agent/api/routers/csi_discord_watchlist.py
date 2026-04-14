from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
from pathlib import Path
import os
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/csi/discord", tags=["csi", "discord", "watchlist"])

def get_discord_watchlist_path() -> Path:
    # Use the same directory as the YouTube watchlist
    path = Path(os.getenv("CSI_DATA_DIR", "/var/lib/universal-agent/csi")).expanduser()
    return path / "discord_watchlist.json"

def _ensure_file_exists(path: Path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"categories": [], "servers": []}, f, indent=2)

@router.get("")
async def get_watchlist():
    """Retrieve the current Discord CSI watchlist."""
    path = get_discord_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        categories = data.setdefault("categories", [])
        servers = data.setdefault("servers", [])
        return {"categories": categories, "servers": servers}
    except Exception as e:
        logger.error(f"Error reading discord watchlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class CategoryRequest(BaseModel):
    name: str

@router.post("/categories")
async def add_category(request: CategoryRequest):
    path = get_discord_watchlist_path()
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
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/categories/{old_name}")
async def rename_category(old_name: str, request: CategoryRequest):
    path = get_discord_watchlist_path()
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
            
        servers = data.get("servers", [])
        for srv in servers:
            if srv.get("domain") == old_name:
                srv["domain"] = request.name
                
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "categories": data["categories"], "servers": data["servers"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/categories/{name}")
async def delete_category(name: str):
    path = get_discord_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if "categories" in data and name in data["categories"]:
            data["categories"].remove(name)
            
        servers = data.get("servers", [])
        data["servers"] = [srv for srv in servers if srv.get("domain") != name]
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        # In the future, we could trigger DB cascading for Discord messages here.
            
        return {"success": True, "categories": data.get("categories", []), "servers": data["servers"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ServerAddRequest(BaseModel):
    server_id: str

@router.post("/add")
async def add_server(request: ServerAddRequest):
    path = get_discord_watchlist_path()
    _ensure_file_exists(path)
    server_id = request.server_id.strip()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        servers = data.setdefault("servers", [])
        if any(s.get("server_id") == server_id for s in servers):
            raise HTTPException(status_code=409, detail="Server is already being monitored.")

        discord_token = os.getenv("DISCORD_BOT_TOKEN")
        server_name = f"Server {server_id}"
        icon_url = ""
        channels = []

        if discord_token:
            headers = {"Authorization": f"Bot {discord_token}"}
            async with httpx.AsyncClient() as client:
                guild_resp = await client.get(f"https://discord.com/api/v10/guilds/{server_id}", headers=headers)
                if guild_resp.status_code == 200:
                    gdata = guild_resp.json()
                    server_name = gdata.get("name", server_name)
                    if gdata.get("icon"):
                        icon_url = f"https://cdn.discordapp.com/icons/{server_id}/{gdata['icon']}.png"
                
                channels_resp = await client.get(f"https://discord.com/api/v10/guilds/{server_id}/channels", headers=headers)
                if channels_resp.status_code == 200:
                    for ch in channels_resp.json():
                        # Only grab text/news channels (types 0 and 5)
                        if ch.get("type") in [0, 5]:
                            channels.append({
                                "channel_id": ch["id"],
                                "channel_name": ch["name"],
                                "is_watched": False
                            })
        else:
            # Fallback stub for UI testing without token
            channels = [
                {"channel_id": f"{server_id}-1", "channel_name": "announcements", "is_watched": False},
                {"channel_id": f"{server_id}-2", "channel_name": "general", "is_watched": False},
                {"channel_id": f"{server_id}-3", "channel_name": "updates", "is_watched": False}
            ]

        new_server = {
            "server_id": server_id,
            "server_name": server_name,
            "domain": "uncategorized",
            "icon_url": icon_url,
            "channels": channels
        }
        servers.append(new_server)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return {"success": True, "server": new_server}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding server: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{server_id}")
async def delete_server(server_id: str):
    path = get_discord_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        initial_count = len(data.get("servers", []))
        data["servers"] = [srv for srv in data.get("servers", []) if srv.get("server_id") != server_id]
        
        if len(data["servers"]) == initial_count:
            raise HTTPException(status_code=404, detail="Server not found.")
            
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "servers": data["servers"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ServerDomainPatch(BaseModel):
    domain: str

@router.patch("/{server_id}")
async def patch_server_domain(server_id: str, request: ServerDomainPatch):
    path = get_discord_watchlist_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        updated = False
        for srv in data.get("servers", []):
            if srv.get("server_id") == server_id:
                srv["domain"] = request.domain
                updated = True
                break
                
        if not updated:
            raise HTTPException(status_code=404, detail="Server not found")
            
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True, "servers": data["servers"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SubChannelToggleRequest(BaseModel):
    is_watched: bool

@router.patch("/{server_id}/channels/{channel_id}")
async def toggle_subchannel(server_id: str, channel_id: str, request: SubChannelToggleRequest):
    path = get_discord_watchlist_path()
    _ensure_file_exists(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        found = False
        for srv in data.get("servers", []):
            if srv.get("server_id") == server_id:
                for ch in srv.get("channels", []):
                    if ch.get("channel_id") == channel_id:
                        ch["is_watched"] = request.is_watched
                        found = True
                        break
                if found:
                    break
                    
        if not found:
            raise HTTPException(status_code=404, detail="Channel or server not found")
            
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
