
import json
import os
import aiofiles
from typing import Dict, Optional, Protocol
import logging

logger = logging.getLogger(__name__)

class SessionStore(Protocol):
    async def get_session(self, chat_id: int) -> Optional[str]:
        ...
    
    async def set_session(self, chat_id: int, session_id: str) -> None:
        ...

class FileSessionStore:
    """
    Simple file-based session store. 
    Maps Telegram Chat ID -> Agent Session ID.
    """
    def __init__(self, filepath: str = ".sessions/telegram.json"):
        self.filepath = filepath
        self._cache: Dict[str, str] = {}
        self._loaded = False
        
        # Ensure dir exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

    async def _load(self):
        if self._loaded:
            return
        
        if not os.path.exists(self.filepath):
            self._cache = {}
            self._loaded = True
            return

        try:
            async with aiofiles.open(self.filepath, mode='r') as f:
                content = await f.read()
                if content:
                    self._cache = json.loads(content)
        except Exception as e:
            logger.error(f"Failed to load sessions from {self.filepath}: {e}")
            self._cache = {}
        
        self._loaded = True

    async def _save(self):
        try:
            async with aiofiles.open(self.filepath, mode='w') as f:
                await f.write(json.dumps(self._cache, indent=2))
        except Exception as e:
            logger.error(f"Failed to save sessions to {self.filepath}: {e}")

    async def get_session(self, chat_id: int) -> Optional[str]:
        await self._load()
        return self._cache.get(str(chat_id))

    async def set_session(self, chat_id: int, session_id: str) -> None:
        await self._load()
        self._cache[str(chat_id)] = session_id
        await self._save()
