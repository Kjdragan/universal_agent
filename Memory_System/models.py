from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class MemoryBlock(BaseModel):
    """
    Represents a Core Memory block (in-context).
    e.g. 'persona', 'human'.
    """
    label: str
    value: str
    is_editable: bool = True
    description: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.now)

class ArchivalItem(BaseModel):
    """
    Represents a single item in Archival Memory (out-of-context).
    """
    content: str
    tags: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)
    item_id: Optional[str] = None  # UUID assigned by storage

class AgentState(BaseModel):
    """
    Overall state of the agent's memory for a user/session.
    """
    core_memory: List[MemoryBlock]
    last_archival_insert: Optional[datetime] = None
