import asyncio
import logging
from typing import Optional, Callable, Awaitable, List

logger = logging.getLogger(__name__)

from contextvars import ContextVar

# ContextVar for the current execution context to support nested runs
_input_handler_var: ContextVar[Optional[Callable[[str, str, Optional[List[str]]], Awaitable[str]]]] = ContextVar("_input_handler", default=None)
# Global fallback handler for contexts that lose ContextVar propagation (e.g. tool threads)
_global_input_handler: Optional[Callable[[str, str, Optional[List[str]]], Awaitable[str]]] = None

def set_input_handler(handler: Optional[Callable[[str, str, Optional[List[str]]], Awaitable[str]]]):
    """Set the active input handler for the current turn."""
    global _global_input_handler
    if handler:
        logger.info("Setting remote input handler")
    else:
        logger.info("Clearing remote input handler")
    _input_handler_var.set(handler)
    _global_input_handler = handler

async def request_user_input(question: str, category: str = "general", options: Optional[List[str]] = None) -> str:
    """
    Request input from the user, using a remote handler if available, 
    otherwise falling back to terminal input.
    """
    handler = _input_handler_var.get()
    if handler:
        logger.info(f"Using remote input handler for question: {question[:50]}...")
        try:
            return await handler(question, category, options)
        except Exception as e:
            logger.error(f"Error in remote input handler: {e}", exc_info=True)
            # Fallback to CLI on handler failure
    elif _global_input_handler:
        logger.info(f"Using global input handler for question: {question[:50]}...")
        try:
            return await _global_input_handler(question, category, options)
        except Exception as e:
            logger.error(f"Error in global input handler: {e}", exc_info=True)
            # Fallback to CLI on handler failure
    
    logger.info("No remote input handler available, falling back to terminal input")
    # Standard terminal fallback
    print(f"\n❓ {question}")
    if options:
        print(f"Options: {', '.join(options)}")
    
    return await asyncio.to_thread(input, "👤 Your answer: ")
