#!/usr/bin/env python3
"""Split a channels_watchlist.json into AI-active + non-AI-dormant by channel_id.

Reversible: writes <path>.bak (original), rewrites <path> with only kept channels,
and writes <dir>/channels_watchlist_dormant.json with the removed channels.
Idempotent-ish: re-running drops already-removed ids (no-op on second run).
"""
import json
import os
import sys


def load(path):
    with open(path) as f:
        return json.load(f)

def channels_of(d, path):
    if isinstance(d, dict) and "channels" in d:
        return d["channels"], "dict"
    if isinstance(d, list):
        return d, "list"
    raise SystemExit(f"unexpected structure in {path!r}: {type(d).__name__}")

def main(path, dormant_ids_file):
    dormant_ids = {l.strip() for l in open(dormant_ids_file) if l.strip()}
    d = load(path)
    chans, shape = channels_of(d, path)
    keep = [c for c in chans if c.get("channel_id") not in dormant_ids]
    drop = [c for c in chans if c.get("channel_id") in dormant_ids]
    if not drop:
        print(f"  {path}: nothing to split (0 of {len(chans)} matched) — skipped")
        return
    # backup once
    bak = path + ".prebackup.json"
    if not os.path.exists(bak):
        with open(bak, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    # rewrite active in place, preserving wrapper
    if shape == "dict":
        d["channels"] = keep
        d["unique_channels"] = len(keep)
        active_obj = d
    else:
        active_obj = keep
    with open(path, "w") as f:
        json.dump(active_obj, f, indent=2, ensure_ascii=False)
    # write dormant sibling
    ddir = os.path.dirname(os.path.abspath(path))
    dormant_path = os.path.join(ddir, "channels_watchlist_dormant.json")
    dormant_obj = {
        "note": "Non-AI channels split out of the RSS watchlist 2026-05-30 (cooking/health/geopolitics/news/finance/hardware/etc.). NOT polled. Reversible: merge entries back into channels_watchlist.json to re-activate.",
        "split_count": len(drop),
        "channels": drop,
    }
    # if a dormant file already exists, union by channel_id
    if os.path.exists(dormant_path):
        prev = load(dormant_path)
        prev_ch = prev.get("channels", []) if isinstance(prev, dict) else prev
        seen = {c.get("channel_id") for c in drop}
        drop = drop + [c for c in prev_ch if c.get("channel_id") not in seen]
        dormant_obj["channels"] = drop
        dormant_obj["split_count"] = len(drop)
    with open(dormant_path, "w") as f:
        json.dump(dormant_obj, f, indent=2, ensure_ascii=False)
    print(f"  {path}: kept {len(keep)}, dormanted {len(drop)} (backup {os.path.basename(bak)}, dormant -> {os.path.basename(dormant_path)})")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
