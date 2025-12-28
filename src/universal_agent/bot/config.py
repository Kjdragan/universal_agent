import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Webhook Secret (for securing the endpoint)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super-secret-token")

# Allowed User IDs (comma-separated list of Telegram User IDs)
ALLOWED_USER_IDS = [
    int(uid.strip()) 
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") 
    if uid.strip()
]

# Webhook URL (Public URL where Telegram sends updates)
# This will be set by the register_webhook.py script or env var
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Port for FastAPI server
PORT = int(os.getenv("PORT", "8000"))

# Task Queue Settings
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "1"))
