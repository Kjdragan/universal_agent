import os
from pathlib import Path

import yaml

from universal_agent.infisical_loader import initialize_runtime_secrets

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

def init_secrets():
    """Ensure Infisical secrets are loaded into os.environ"""
    # Safe fallback if not already injected
    if not os.environ.get("DISCORD_USER_TOKEN"):
        try:
            initialize_runtime_secrets(force_reload=False)
        except Exception as e:
            print(f"Warning: Failed to initialize infisical secrets: {e}")

def get_db_path():
    db_name = CONFIG.get("database", {}).get("path", "discord_intelligence.db")
    return str(BASE_DIR / db_name)

def get_discord_token():
    token = os.environ.get("DISCORD_USER_TOKEN")
    if token:
        return token
    raise ValueError("DISCORD_USER_TOKEN not set in environment or Infisical")
