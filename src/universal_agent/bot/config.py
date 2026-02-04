
import os
from typing import List
from dotenv import load_dotenv

# Load .env file
load_dotenv()

def get_allowed_user_ids() -> List[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    if not raw:
        return []
    return [int(uid.strip()) for uid in raw.split(",") if uid.strip()]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = get_allowed_user_ids()
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
