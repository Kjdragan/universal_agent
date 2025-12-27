from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    app_name: str = "LogfireFetch Service"
    logfire_read_token: str
    
    # Optional: Webhook secret if we implement auth later
    webhook_secret: Optional[str] = None
    
    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache()
def get_settings():
    return Settings()
