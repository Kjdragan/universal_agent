
from mcp.server.fastmcp import FastMCP
import asyncio
import os
import logging
from telegram import Bot
from telegram.error import TelegramError

# Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(uid) for uid in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if uid]

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server_telegram")

mcp = FastMCP("Telegram Toolkit")

def _validate_config():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set.")

@mcp.tool() # Sync wrapper mainly because FastMCP handles async? 
# Actually FastMCP supports async def tools.
async def telegram_send_message(chat_id: int, text: str) -> str:
    """
    Send a message to a Telegram chat.
    
    Args:
        chat_id: The target chat ID (integer).
        text: The message content (Markdown supported).
    """
    _validate_config()
    if ALLOWED_USER_IDS and chat_id not in ALLOWED_USER_IDS:
        return f"Error: Chat ID {chat_id} is not in allowed list."

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        return f"Message sent to {chat_id}"
    except Exception as e:
        return f"Error sending message: {str(e)}"

@mcp.tool()
async def telegram_get_updates(limit: int = 5) -> str:
    """
    [DEPRECATED] Get recent messages. 
    WARNING: Do not use this if the main Bot is running, as it will conflict with the update poller.
    This tool exists only for debugging when the main bot is stopped.
    """
    _validate_config()
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        # Note: This might fail if another poller is active
        updates = await bot.get_updates(limit=limit, timeout=5)
        
        if not updates:
            return "No new messages."
            
        result = []
        for u in updates:
            if u.message:
                sender = u.message.from_user
                sender_id = sender.id
                if ALLOWED_USER_IDS and sender_id not in ALLOWED_USER_IDS:
                    continue
                    
                result.append(f"[{u.message.date.strftime('%Y-%m-%d %H:%M')}] From {sender.first_name} (ID: {sender_id}): {u.message.text}")
                
        return "\n".join(result) if result else "No relevant messages found."

    except Exception as e:
        return f"Error getting updates (likely conflict with running Bot): {str(e)}"

if __name__ == "__main__":
    mcp.run()
