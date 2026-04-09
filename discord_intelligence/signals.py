import re
from .config import CONFIG

RELEASE_PATTERN = re.compile(r'\b(v?\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?)\b', re.IGNORECASE)

# Word-boundary patterns for time indicators to prevent substring matches
# e.g. "am" should not match "program", "pm" should not match "spam"
_TIME_INDICATOR_PATTERNS = [
    re.compile(r'\btoday\s+at\b', re.IGNORECASE),
    re.compile(r'\btomorrow\s+at\b', re.IGNORECASE),
    re.compile(r'\bmonday\b', re.IGNORECASE),
    re.compile(r'\btuesday\b', re.IGNORECASE),
    re.compile(r'\bwednesday\b', re.IGNORECASE),
    re.compile(r'\bthursday\b', re.IGNORECASE),
    re.compile(r'\bfriday\b', re.IGNORECASE),
    re.compile(r'\bsaturday\b', re.IGNORECASE),
    re.compile(r'\bsunday\b', re.IGNORECASE),
    re.compile(r'\d{1,2}\s*(?::\d{2})?\s*[ap]\.?m\.?\b', re.IGNORECASE),  # "3pm", "3:00 PM", "3 p.m."
    re.compile(r'\b[ap]\.?m\.?\s', re.IGNORECASE),                         # standalone "AM " / "PM "
    re.compile(r'\bUTC\b'),
    re.compile(r'\bEST\b'),
    re.compile(r'\bPST\b'),
    re.compile(r'\bCET\b'),
    re.compile(r'\bGMT\b'),
]

# Event words as word-boundary patterns — excludes "call" which matches casual speech
_EVENT_WORD_PATTERNS = [
    re.compile(r'\bevent\b', re.IGNORECASE),
    re.compile(r'\bAMA\b'),                          # case-sensitive: "AMA" not "llama"
    re.compile(r'\btownhall\b', re.IGNORECASE),
    re.compile(r'\btown\s+hall\b', re.IGNORECASE),
    re.compile(r'\bmeeting\b', re.IGNORECASE),
    re.compile(r'\bwebinar\b', re.IGNORECASE),
    re.compile(r'\boffice\s+hours\b', re.IGNORECASE),
    re.compile(r'\blive\s+stream\b', re.IGNORECASE),
    re.compile(r'\bworkshop\b', re.IGNORECASE),
    re.compile(r'\bconference\b', re.IGNORECASE),
]

# Noise indicators — messages with these are debug/support chatter, not announcements
_NOISE_INDICATORS = re.compile(
    r'(?:cargo\s+install|npm\s+install|pip\s+install|stack\s+trace|exception|traceback|```\s*\n.*error)',
    re.IGNORECASE | re.DOTALL
)

# Release announcement words — require word-boundary matching
_RELEASE_WORDS = [
    re.compile(r'\brelease[ds]?\b', re.IGNORECASE),
    re.compile(r'\blaunch(?:ed|ing)?\b', re.IGNORECASE),
    re.compile(r'\bdeployed\b', re.IGNORECASE),
    re.compile(r'\bpublished\b', re.IGNORECASE),
    re.compile(r'\bnew\s+version\b', re.IGNORECASE),
    re.compile(r'\bchangelog\b', re.IGNORECASE),
    re.compile(r'\bupdate[ds]?\b', re.IGNORECASE),
    re.compile(r'\b(?:patch|minor|major)\s+release\b', re.IGNORECASE),
]


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

    # Rule 2: Version + release-announcement detection (context-aware)
    if RELEASE_PATTERN.search(message_content):
        # Must also contain a release-announcement word
        has_release_word = any(p.search(message_content) for p in _RELEASE_WORDS)
        if has_release_word:
            # Suppress if the message looks like debug/install terminal output
            # Lines starting with code fences, paths, or install commands are noise
            is_noise = bool(_NOISE_INDICATORS.search(message_content))
            # Also suppress if the message is predominantly a code block (>60% inside ```)
            code_block_chars = sum(len(b) for b in re.findall(r'```.*?```', message_content, re.DOTALL))
            is_mostly_code = code_block_chars > len(message_content) * 0.6 if message_content else False
            
            if not is_noise and not is_mostly_code:
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

    # Rule 4: Text-based event detection (tightened to reduce false positives)
    # Requirements:
    #   - Message must be at least 30 chars (short messages are rarely event announcements)
    #   - Must contain an event-type word (word-boundary matched)
    #   - Must contain a time indicator (word-boundary matched)
    #   - Must NOT be predominantly a code block
    if len(message_content) >= 30:
        has_event_word = any(p.search(message_content) for p in _EVENT_WORD_PATTERNS)
        has_time_indicator = any(p.search(message_content) for p in _TIME_INDICATOR_PATTERNS)
        
        if has_event_word and has_time_indicator:
            # Final guard: skip if message is mostly code
            code_block_chars = sum(len(b) for b in re.findall(r'```.*?```', message_content, re.DOTALL))
            is_mostly_code = code_block_chars > len(message_content) * 0.5 if message_content else False
            if not is_mostly_code:
                signals.append({
                    "rule_matched": "text_event_detected",
                    "severity": "medium",
                    "layer": "Layer2"
                })

    return signals
