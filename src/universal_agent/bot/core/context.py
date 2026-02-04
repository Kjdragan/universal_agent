
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from telegram import Update
from telegram.ext import ContextTypes

@dataclass
class BotContext:
    """
    Extended context object passed through the middleware chain.
    """
    update: Update
    ptb_context: ContextTypes.DEFAULT_TYPE
    task_manager: Any = None
    session_id: Optional[str] = None
    user_data: Dict[str, Any] = field(default_factory=dict)
    aborted: bool = False
    
    def abort(self):
        """Stop processing deeper middlewares."""
        self.aborted = True
