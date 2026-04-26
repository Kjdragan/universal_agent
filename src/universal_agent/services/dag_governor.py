"""
DAG Concurrency Governor — manages global concurrency limits for ZAI DAG executions.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DAG_MAX_CONCURRENCY = 2

class DagConcurrencyGovernor:
    """System-level concurrency limit for DAG operations."""
    
    _instance: Optional["DagConcurrencyGovernor"] = None

    def __init__(self, max_concurrent: Optional[int] = None):
        self._max_concurrent = max_concurrent or int(
            os.getenv("UA_DAG_MAX_CONCURRENCY", str(DEFAULT_DAG_MAX_CONCURRENCY))
        )
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        
    @classmethod
    def get_instance(cls, **kwargs) -> "DagConcurrencyGovernor":
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance
        
    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None
        
    @asynccontextmanager
    async def acquire_slot(self):
        """Acquire a concurrency slot for DAG execution."""
        await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()
