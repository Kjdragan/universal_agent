import os
import json
import logfire
from .db import LosslessDB
import asyncio

# A global fallback instance if someone forgets to pass a DB
_GLOBAL_DB = None

def get_global_db():
    global _GLOBAL_DB
    if not _GLOBAL_DB:
        path = os.getenv("UA_LOSSLESS_DB_PATH", os.path.expanduser("~/.universal_agent/lcm.db"))
        _GLOBAL_DB = LosslessDB(path)
    return _GLOBAL_DB


class LosslessMessageHistory:
    """
    Drop-in replacement for utils.MessageHistory.
    Instead of truncating permanently, it relies on the SQLite LCM database and DAG condenser.
    """
    
    def __init__(self, system_prompt_tokens: int = 2000, session_id: str = None):
        if not session_id:
            session_id = f"anonymous_{os.getpid()}"
            
        self.db = get_global_db()
        self.session_id = session_id
        self.conv_id = self.db.get_or_create_conversation(session_id)
        
        self.total_tokens = system_prompt_tokens
        self._system_prompt_tokens = system_prompt_tokens
        self._truncation_count = 0
        
    def add_message(self, role: str, content, usage=None) -> None:
        """
        Overrides the standard add_message.
        Instead of appending to an in-memory list, we serialize the blocks and persist to SQLite.
        """
        # Calculate tokens
        estimated = 0
        raw_blocks = []
        if isinstance(content, str):
            estimated = len(content) // 4
            text_content = content
        elif isinstance(content, list):
            # Complex blocks
            try:
                # E.g. [{"type": "text", "text": "foo"}, {"type": "tool_call", ...}]
                # Try to serialize them if they are objects
                raw_blocks = [
                    blk if isinstance(blk, dict) else (blk.__dict__ if hasattr(blk, '__dict__') else str(blk))
                    for blk in content
                ]
                text_content = json.dumps(raw_blocks)[:1000] # short version for indexing
            except Exception:
                raw_blocks = [{"type": "text", "text": str(content)}]
                text_content = str(content)
            estimated = len(str(raw_blocks)) // 4
        else:
            text_content = str(content)
            estimated = len(text_content) // 4
            
        if usage:
            inp = getattr(usage, "input_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or 0
            estimated = inp + out

        self.db.insert_message(
            conversation_id=self.conv_id, 
            role=role, 
            content=text_content, 
            raw_blocks=raw_blocks, 
            token_count=estimated
        )
        self.total_tokens += estimated

    def truncate(self) -> bool:
        """
        Replaces standard truncation. 
        Instead of popping the oldest messages, this triggers `run_compaction_sweep`.
        Returns True if condensation happened.
        """
        threshold = int(os.environ.get("UA_TRUNCATION_THRESHOLD", 150000))
        if self.total_tokens <= threshold:
            return False
            
        from .dag_condenser import run_compaction_sweep
        
        # Async invocation hook inside sync function (fire and forget or run via loop if possible)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(run_compaction_sweep(self.db, self.conv_id))
        except RuntimeError:
            # no running loop, block and run
            asyncio.run(run_compaction_sweep(self.db, self.conv_id))
            
        self._truncation_count += 1
        return True

    def should_handoff(self) -> bool:
        """Lossless never forces a fatal handoff (except infinite limits)."""
        limit = int(os.environ.get("UA_CONTEXT_WINDOW", 200000))
        return self.total_tokens >= limit
        
    def get_stats(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "message_count": len(self.db.get_context_items(self.conv_id)),
            "truncation_count": self._truncation_count,
            "lossless_mode": True
        }
        
    def format_for_api(self) -> list[dict]:
        """
        Reassembles context from DB.
        Summaries are exported as XML strings inside user roles.
        Messages are parsed back into dictionaries or API native formatting.
        """
        items = self.db.get_context_items(self.conv_id)
        api_messages = []
        
        for item in items:
            itype = item["type"]
            data = item["data"]
            
            if itype == "summary":
                # Inject as XML block in a user message
                api_messages.append({
                    "role": "user",
                    "content": f'<summary id="{data["id"]}" depth="{data["depth"]}">{data["content"]}</summary>'
                })
            else:
                # Raw message
                role = data["role"]
                # If raw_blocks are available, parse them back
                if data["raw_blocks"] and data["raw_blocks"] != "[]":
                    blocks = json.loads(data["raw_blocks"])
                    api_messages.append({"role": role, "content": blocks})
                else:
                    api_messages.append({"role": role, "content": data["content"]})
                    
        return api_messages

    def get_messages(self) -> list[dict]:
        return self.format_for_api()
        
    def reset(self) -> None:
        self.total_tokens = self._system_prompt_tokens
