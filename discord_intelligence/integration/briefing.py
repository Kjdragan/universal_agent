import os
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def generate_daily_briefing(db):
    """
    Generates a markdown snippet summarizing the past 24 hours of Discord activity
    for ingestion into the main UA Morning Briefing.
    """
    # Simply write insights to a staging file that Proactive Advisor can pick up
    try:
        base_dir = Path(__file__).resolve().parent.parent
        briefings_dir = base_dir / "briefings"
        briefings_dir.mkdir(exist_ok=True)
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = briefings_dir / f"briefing_{date_str}.md"
        
        with open(file_path, "w") as f:
             f.write(f"## Community Discord Pulse ({date_str})\n\n")
             f.write("New insights detected today will appear here.\n")
             # In a real implementation we would SELECT * FROM insights WHERE created_at > (now - 24h)
        
        logger.info(f"Generated daily briefing snippet: {file_path}")
        return str(file_path)
    except Exception as e:
        logger.error(f"Failed to generate briefing: {e}")
        return None
