"""
Centralized rate limiter for ZAI API calls.

This module provides a singleton rate limiter that:
- Enforces global concurrency limits across all components
- Uses adaptive backoff that adjusts based on 429 frequency
- Adds jitter to prevent thundering herd
- Logs rate limit events to logfire for monitoring

Configuration (environment variables):
    ZAI_MAX_CONCURRENT: Max parallel requests (default: 3)
    ZAI_INITIAL_BACKOFF: Initial backoff floor in seconds (default: 5.0)
    ZAI_MAX_BACKOFF: Maximum backoff cap in seconds (default: 30.0)
    ZAI_MIN_INTERVAL: Minimum seconds between request starts (default: 1.0)
"""

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

try:
    import logfire
except ImportError:
    logfire = None


class ZAIRateLimiter:
    """
    Centralized rate limiter for ZAI API calls.
    
    Features:
    - Global concurrency limit (default 3)
    - Adaptive backoff that increases floor after repeated 429s
    - Staggered release with jitter to prevent thundering herd
    - Shared state across all callers
    - Logfire instrumentation for monitoring
    """
    
    _instance: Optional["ZAIRateLimiter"] = None
    _lock: asyncio.Lock = None
    
    def __init__(self, max_concurrent: int = None):
        # Config from environment
        # Default to 2 concurrent - ZAI rate limits are strict
        self._max_concurrent = max_concurrent or int(os.getenv("ZAI_MAX_CONCURRENT", "2"))
        self._initial_backoff = float(os.getenv("ZAI_INITIAL_BACKOFF", "1.0"))
        self._max_backoff = float(os.getenv("ZAI_MAX_BACKOFF", "30.0"))
        
        # State
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._backoff_floor = self._initial_backoff
        self._last_429_time = 0.0
        self._consecutive_429s = 0
        self._total_429s = 0
        self._total_requests = 0
        
        # Minimum inter-request spacing to avoid burst rate limits
        self._min_request_interval = float(os.getenv("ZAI_MIN_INTERVAL", "0.5"))
        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        
        if logfire:
            logfire.info(
                "zai_rate_limiter_initialized",
                max_concurrent=self._max_concurrent,
                initial_backoff=self._initial_backoff,
                max_backoff=self._max_backoff,
            )
    
    @classmethod
    def get_instance(cls, max_concurrent: int = None) -> "ZAIRateLimiter":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(max_concurrent)
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset the singleton (useful for testing)."""
        cls._instance = None
    
    async def record_429(self, context: str = ""):
        """
        Called when a 429 is received. Adjusts adaptive backoff.
        
        Args:
            context: Optional context string for logging (e.g., section name)
        """
        async with self._state_lock:
            now = time.time()
            self._total_429s += 1
            
            # If 429s are happening within 10s of each other, they're related
            if now - self._last_429_time < 10:
                self._consecutive_429s += 1
                # Raise floor after repeated 429s (max 8s floor)
                self._backoff_floor = min(8.0, self._initial_backoff * (1.5 ** self._consecutive_429s))
            else:
                self._consecutive_429s = 1
                self._backoff_floor = self._initial_backoff
            
            self._last_429_time = now
            
            if logfire:
                logfire.warn(
                    "zai_rate_limit_hit",
                    context=context,
                    consecutive_429s=self._consecutive_429s,
                    total_429s=self._total_429s,
                    backoff_floor=self._backoff_floor,
                )
    
    async def record_success(self):
        """Called on successful request. Gradually lowers backoff floor."""
        async with self._state_lock:
            self._total_requests += 1
            # Slowly decay the floor back to initial
            self._backoff_floor = max(self._initial_backoff, self._backoff_floor * 0.9)
            self._consecutive_429s = max(0, self._consecutive_429s - 1)
    
    def get_backoff(self, attempt: int) -> float:
        """
        Calculate backoff with jitter. Uses adaptive floor.
        
        Args:
            attempt: Zero-indexed attempt number
            
        Returns:
            Backoff duration in seconds
        """
        base = self._backoff_floor * (2 ** attempt)
        jitter = random.uniform(0.1, 0.5) * base
        return min(base + jitter, self._max_backoff)
    
    @asynccontextmanager
    async def acquire(self, context: str = ""):
        """
        Acquire a slot for an API call.
        Enforces both concurrent limit AND minimum inter-request spacing.
        
        Args:
            context: Optional context string for logging
        """
        await self._semaphore.acquire()
        try:
            # Enforce minimum spacing between ALL requests (not just concurrent)
            # This prevents burst rate limits from sliding window quotas
            async with self._request_lock:
                now = time.time()
                elapsed = now - self._last_request_time
                if elapsed < self._min_request_interval:
                    wait_time = self._min_request_interval - elapsed
                    # Add small jitter to prevent exact synchronization
                    wait_time += random.uniform(0.05, 0.15)
                    await asyncio.sleep(wait_time)
                self._last_request_time = time.time()
            yield
        finally:
            self._semaphore.release()
    
    def get_stats(self) -> dict:
        """Return current rate limiter statistics."""
        return {
            "max_concurrent": self._max_concurrent,
            "backoff_floor": self._backoff_floor,
            "consecutive_429s": self._consecutive_429s,
            "total_429s": self._total_429s,
            "total_requests": self._total_requests,
        }


async def with_rate_limit_retry(
    func,
    *args,
    max_retries: int = 5,
    context: str = "",
    **kwargs
):
    """
    Execute an async function with rate limit handling.
    
    This is a convenience wrapper that:
    1. Acquires a slot from the rate limiter
    2. Executes the function
    3. Handles 429 errors with adaptive backoff
    4. Records success/failure for adaptive tuning
    
    Args:
        func: Async function to execute
        *args: Positional arguments for func
        max_retries: Maximum retry attempts (default: 5)
        context: Context string for logging
        **kwargs: Keyword arguments for func
        
    Returns:
        Result of func
        
    Raises:
        Last exception if all retries exhausted
    """
    limiter = ZAIRateLimiter.get_instance()
    last_error = None
    
    for attempt in range(max_retries):
        async with limiter.acquire(context):
            try:
                result = await func(*args, **kwargs)
                await limiter.record_success()
                return result
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                
                if is_rate_limit:
                    await limiter.record_429(context)
                    last_error = e
                    
                    if attempt < max_retries - 1:
                        delay = limiter.get_backoff(attempt)
                        print(f"  ⚠️ [429] Rate limited ({context}). Backoff: {delay:.1f}s (Attempt {attempt+1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                else:
                    # Non-rate-limit error, don't retry
                    raise
    
    # All retries exhausted
    if last_error:
        raise last_error
    raise RuntimeError(f"Rate limit retries exhausted for {context}")
