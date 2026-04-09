import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def upsert_to_wiki(topic: str, content: str):
    """
    Given a high-confidence insight, push it to the LLM-readable wiki format 
    (stubbed for now into a local wiki_staging file).
    """
    try:
        base_dir = Path(__file__).resolve().parent.parent
        wiki_dir = base_dir / "wiki_staging"
        wiki_dir.mkdir(exist_ok=True)
        
        safe_topic = "".join(c for c in topic if c.isalnum() or c in " -_").strip()
        file_path = wiki_dir / f"{safe_topic}.md"
        
        with open(file_path, "w") as f:
             f.write(f"# {topic}\n\n{content}\n")
             
        logger.info(f"Upserted insight to wiki: {file_path}")
    except Exception as e:
        logger.error(f"Failed to upsert to wiki: {e}")
