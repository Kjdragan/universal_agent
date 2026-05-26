"""One-shot: upgrade channels_watchlist.json with tier/counter fields and
seed the 22 gold + 4 blocked channels.

Idempotent — running it twice produces no diff.

After this lands in main and deploys, the schema is in place forever; this
script is kept around as a reference for the migration shape, not because we
need to run it again.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Hand-curated lists from the 2026-05-22 conversation.
# Gold = auto-add to that day's playlist when a new video is published.
# Each entry is (channel_name_substring_match, channel_id_optional).
# We match by exact channel_id when known, else by exact channel_name.

GOLD_CHANNELS: list[tuple[str, str | None]] = [
    # ai_coding_and_agents
    ("AICodeKing", "UC0m81bQuthaQZmFbXEY9QSw"),
    ("Cole Medin", None),
    ("Prompt Engineering", None),
    ("Adam Lucek", None),
    ("Ray Fernando", None),
    ("AI Jason", "UCrXSVX9a1mj8l0CMLwKgMVw"),
    ("Chris Hay", None),
    # ai_news_and_business
    ("All About AI", "UCR9j1jqqB5Rse69wjUnbYwA"),
    ("Wes Roth", "UCqcbQf6yw5KzRoDDcZ_wBSw"),
    ("TheAIGRID", "UCbY9xX3_jW5c2fjlZVBI4cg"),
    ("Sam Witteveen", "UC55ODQSvARtgSyc8ThfiepQ"),
    ("Matthew Berman", None),
    ("Discover AI", None),
    # ai_models_and_research
    ("Anthropic", "UCrDwWp7EBBv4NwvScIpBDOA"),
    ("Latent Space", "UCxBcwypKK-W3GHd_RZ9FZrQ"),
    ("Simon Willison", "UCPzGwk1N5ea7sV3S2c2x1kg"),
    # longform_interviews
    ("Dwarkesh Patel", "UCXl4i9dYBrFOabk0xGmbkRA"),
    ("Lex Fridman", "UCSHZKyawb77ixDdsGog4iWA"),
    # software_engineering
    ("IndyDevDan", "UC_x36zCEGilGpB1m-V4gmjg"),
    # other_signal
    ("AI Engineer", "UCLKPca3kwwd-B59HNr-_lvA"),
    # geopolitics_and_conflict
    ("Pyotr Kurzin | Geopolitics", "UC7-TPqKMViddKZeo4K2P2jg"),
    # cooking
    ("Brian Lagerstrom", "UCn5fhcGRrCvrmFibPbT6q1A"),
]

# Permanently blocked — never appear in digest or sidecar.
BLOCKED_CHANNELS: list[tuple[str, str | None]] = [
    ("LangChain", "UCC-lyoTfSrcJzA1ab3APAgw"),
    ("Fahd Mirza", "UCPix8N6PMRI4KzgyjuZeF0g"),
    ("MCP Developers Summit", "UCgkApalw5crKXOtr_mqtPQg"),
    ("Cloudflare Developers", "UC3QIolTSR29ba4_u15vtEUQ"),
]

# Lex Fridman gets a generous duration cap (his interviews are routinely 2-4
# hours, occasionally 5+); every other channel inherits the global pre-ingest
# cap (5400s today). 86400 = 24 hours, which is effectively "unlimited" for
# YouTube content while keeping the schema field a simple positive int rather
# than introducing a null/sentinel semantics quirk.
PER_CHANNEL_DURATION_OVERRIDE: dict[str, int] = {
    "UCSHZKyawb77ixDdsGog4iWA": 86400,  # Lex Fridman — 24h cap (effectively unlimited)
}

# New fields added to every channel record (default values).
NEW_FIELDS_TEMPLATE: dict = {
    "tier": "sidecar",
    "manual_add_count_30d": 0,
    "sidecar_approval_count_30d": 0,
    "last_publication_seen_at": None,
    "last_promoted_to_gold_at": None,
    "duration_max_seconds_override": None,  # null = inherit global cap
}


def _find_channel(channels: list[dict], name_match: str, channel_id: str | None) -> dict | None:
    """Locate a channel by exact channel_id if provided, else by exact channel_name."""
    if channel_id:
        for c in channels:
            if c.get("channel_id") == channel_id:
                return c
        return None
    for c in channels:
        if c.get("channel_name") == name_match:
            return c
    return None


def migrate(path: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Apply schema + tier seed in place. Returns counts for verification."""
    data = json.loads(path.read_text(encoding="utf-8"))
    channels = data.get("channels", [])

    # 1) Backfill new fields on every channel.
    for c in channels:
        for key, default in NEW_FIELDS_TEMPLATE.items():
            c.setdefault(key, default)

    # 2) Apply gold seed.
    gold_set: set[str] = set()
    gold_missing: list[str] = []
    for name_match, channel_id in GOLD_CHANNELS:
        match = _find_channel(channels, name_match, channel_id)
        if match is None:
            gold_missing.append(f"{name_match} ({channel_id or '?'})")
            continue
        match["tier"] = "gold"
        gold_set.add(match["channel_id"])
        # Apply per-channel duration override if specified. None/missing on a
        # record means "inherit the global pre-ingest cap" (currently 5400s).
        # A specific positive int N means "use N as the cap." 86400 (24h) is
        # used for channels we want effectively uncapped (e.g. Lex Fridman).
        override = PER_CHANNEL_DURATION_OVERRIDE.get(match["channel_id"])
        if override is not None:
            match["duration_max_seconds_override"] = override

    # 3) Apply blocked seed.
    blocked_set: set[str] = set()
    blocked_missing: list[str] = []
    for name_match, channel_id in BLOCKED_CHANNELS:
        match = _find_channel(channels, name_match, channel_id)
        if match is None:
            blocked_missing.append(f"{name_match} ({channel_id or '?'})")
            continue
        match["tier"] = "blocked"
        blocked_set.add(match["channel_id"])

    # 4) Recompute counts.
    summary = {
        "total_channels": len(channels),
        "gold": sum(1 for c in channels if c.get("tier") == "gold"),
        "blocked": sum(1 for c in channels if c.get("tier") == "blocked"),
        "sidecar": sum(1 for c in channels if c.get("tier") == "sidecar"),
        "gold_missing_count": len(gold_missing),
        "blocked_missing_count": len(blocked_missing),
    }

    if gold_missing:
        print(f"WARNING: {len(gold_missing)} gold channels not found in watchlist:", file=sys.stderr)
        for m in gold_missing:
            print(f"  - {m}", file=sys.stderr)
    if blocked_missing:
        print(f"WARNING: {len(blocked_missing)} blocked channels not found:", file=sys.stderr)
        for m in blocked_missing:
            print(f"  - {m}", file=sys.stderr)

    if not dry_run:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "channels_watchlist.json",
        help="Path to channels_watchlist.json (defaults to repo root).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = migrate(args.path, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
