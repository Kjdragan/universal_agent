import re
from .config import CONFIG

RELEASE_PATTERN = re.compile(r'\b(v?\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?)\b', re.IGNORECASE)

def detect_signals(message_content: str, channel_tier: str, author_id: str = None) -> list:
    """
    Evaluates a message's content deterministically.
    Returns a list of matching signals, each as a dict:
      { "rule_matched": str, "severity": str, "layer": "Layer2" }
    """
    signals = []
    
    # Rule 1: Tier A channels matching (implied heavily weighted, we just tag if it's Tier A)
    if channel_tier == 'A':
        signals.append({
            "rule_matched": "tier_a_activity",
            "severity": "high",
            "layer": "Layer2"
        })

    # Rule 2: Version and release pattern matching
    if RELEASE_PATTERN.search(message_content):
        # We might only care if it also includes release words
        release_words = ["release", "launch", "deployed", "published", "new version", "changelog"]
        if any(w in message_content.lower() for w in release_words):
            signals.append({
                "rule_matched": "release_detected",
                "severity": "high",
                "layer": "Layer2"
            })

    # Rule 3: Match tracked interest terms
    keywords = CONFIG.get("keywords", [])
    matched_kws = [kw for kw in keywords if kw.lower() in message_content.lower()]
    if matched_kws:
        signals.append({
            "rule_matched": f"keywords_matched:{','.join(matched_kws)}",
            "severity": "medium",
            "layer": "Layer2"
        })

    return signals
