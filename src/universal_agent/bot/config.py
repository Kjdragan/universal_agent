import os
from typing import List
from dotenv import load_dotenv
import logging

# Load .env file
load_dotenv()

logger = logging.getLogger(__name__)

def _telegram_allowed_user_ids_raw() -> str:
    primary = (os.getenv("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    if primary:
        return primary
    legacy = (os.getenv("ALLOWED_USER_IDS") or "").strip()
    if legacy:
        logger.warning("ALLOWED_USER_IDS is deprecated; use TELEGRAM_ALLOWED_USER_IDS instead")
    return legacy

def get_allowed_user_ids() -> List[int]:
    raw = _telegram_allowed_user_ids_raw()
    if not raw:
        return []
    return [int(uid.strip()) for uid in raw.split(",") if uid.strip()]

def get_telegram_bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


SESSION_FILE_PATH = os.path.join(os.getcwd(), ".sessions/telegram.json")

# Gateway Config
UA_GATEWAY_URL = os.getenv("UA_GATEWAY_URL")
UA_TELEGRAM_ALLOW_INPROCESS = os.getenv("UA_TELEGRAM_ALLOW_INPROCESS", "1") == "1" # Default to 1 for now? Or 0? 
# In agent_adapter logic:
# if not UA_TELEGRAM_ALLOW_INPROCESS: raise ...
# "Set UA_TELEGRAM_ALLOW_INPROCESS=1 for local dev."
# So strict default might be better, but let's emulate legacy behavior.
# Legacy seemingly worked locally.

MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "5"))
