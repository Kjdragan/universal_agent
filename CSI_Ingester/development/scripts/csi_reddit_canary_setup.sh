#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/universal_agent/CSI_Ingester/development"
CONFIG_PATH="${ROOT}/config/config.yaml"
WATCHLIST_PATH="${ROOT}/reddit_watchlist.json"
MODE="${1:-enable}"

if [[ "$MODE" != "enable" && "$MODE" != "disable" ]]; then
  echo "Usage: $0 [enable|disable]"
  exit 2
fi

if [[ "$MODE" == "enable" && ! -f "$WATCHLIST_PATH" ]]; then
  cat >"$WATCHLIST_PATH" <<'JSON'
{
  "subreddits": [
    {"name": "artificial", "category_hint": "ai"},
    {"name": "LocalLLaMA", "category_hint": "ai"},
    {"name": "geopolitics", "category_hint": "political"},
    {"name": "WarCollege", "category_hint": "war"}
  ]
}
JSON
fi

MODE_ENV="$MODE" python3 - <<'PY'
from pathlib import Path
import os
import yaml

config_path = Path('/opt/universal_agent/CSI_Ingester/development/config/config.yaml')
mode = os.environ.get("MODE_ENV", "enable").strip().lower()
payload = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
if not isinstance(payload, dict):
    raise SystemExit('invalid config payload')

sources = payload.setdefault('sources', {})
if not isinstance(sources, dict):
    raise SystemExit('sources config must be object')

reddit = sources.setdefault('reddit_discovery', {})
if not isinstance(reddit, dict):
    raise SystemExit('reddit_discovery config must be object')

reddit.setdefault('poll_interval_seconds', 300)
reddit.setdefault('limit', 30)
reddit.setdefault('user_agent', 'CSIIngester/1.0 (by u/csi_ingester)')
reddit.setdefault('watchlist_file', '/opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json')
reddit.setdefault('seed_on_first_run', True)
reddit.setdefault('max_seen_cache_per_subreddit', 2000)
reddit.setdefault('subreddits', [])

reddit['enabled'] = (mode == 'enable')
config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding='utf-8')
print(f'REDDIT_CANARY_ENABLED={1 if reddit["enabled"] else 0}')
print(f'REDDIT_CANARY_CONFIG={config_path}')
print(f'REDDIT_CANARY_WATCHLIST={reddit.get("watchlist_file","")}')
PY

systemctl restart csi-ingester

echo "CSI=$(systemctl is-active csi-ingester)"
journalctl -u csi-ingester --since '2 minutes ago' --no-pager | tail -n 80
