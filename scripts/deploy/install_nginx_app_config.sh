#!/usr/bin/env bash
#
# install_nginx_app_config.sh — install the app.clearspringcg.com nginx vhost
# from the repo's canonical copy, validate, and reload nginx.
#
# WHY THIS EXISTS
#   The /briefs/* route (the "Read full brief →" links in the hourly intel
#   digest) is served by the UA gateway on :8002, NOT the Next.js frontend on
#   :3000. nginx needs an explicit `location ^~ /briefs/` proxy block or those
#   links fall through to `location /` (Next.js) and return a 404 page.
#
#   That block was originally a hand-edit that lived ONLY on the VPS
#   (/etc/nginx/sites-enabled/universal-agent-app) and was never captured in the
#   repo — classic config drift. A VPS rebuild or nginx reprovision would have
#   silently broken every brief link again. This script makes
#   deploy/nginx/universal-agent-app the single source of truth and the apply
#   path reproducible (one command instead of a manual runbook).
#
# USAGE (on the VPS — run as `ua` (passwordless sudo) or as root):
#   scripts/deploy/install_nginx_app_config.sh            # install + reload
#   scripts/deploy/install_nginx_app_config.sh --check    # diff + nginx -t only, no changes
#
# Idempotent: re-running with no source change still validates + reloads safely.
# On `nginx -t` failure the previous config is restored automatically.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC="$REPO_ROOT/deploy/nginx/universal-agent-app"
AVAIL="/etc/nginx/sites-available/universal-agent-app"
ENABLED="/etc/nginx/sites-enabled/universal-agent-app"
# Backups MUST live outside any nginx include glob (sites-available/* is not
# globbed, sites-enabled/* IS) — a backup dropped under sites-enabled/ would be
# loaded as a duplicate server block ("conflicting server name").
BACKUP_DIR="/var/backups/ua-nginx"
TS="$(date +%Y-%m-%d_%H%M%S)"

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

[[ -f "$SRC" ]] || { echo "ERROR: source config not found: $SRC" >&2; exit 1; }

echo "== nginx app vhost installer =="
echo "source : $SRC"
echo "avail  : $AVAIL"
echo "enabled: $ENABLED"

# Fail closed: the repo config MUST carry the /briefs/ -> 8002 block, otherwise
# installing it would re-introduce the exact regression this script guards.
if ! grep -q 'location \^~ /briefs/' "$SRC" || ! grep -q '127.0.0.1:8002' "$SRC"; then
  echo "ERROR: repo config is missing the /briefs/ -> 8002 block; refusing to install." >&2
  exit 1
fi

# Informational diff: live sites-available vs repo source.
if [[ -f "$AVAIL" ]]; then
  if diff -u "$AVAIL" "$SRC" >"/tmp/nginx_app_diff.$$" 2>/dev/null; then
    echo "sites-available already matches repo (no content change)."
  else
    echo "--- diff (live sites-available vs repo source) ---"
    cat "/tmp/nginx_app_diff.$$" || true
    echo "--------------------------------------------------"
  fi
  rm -f "/tmp/nginx_app_diff.$$"
else
  echo "sites-available does not exist yet (fresh install)."
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "[--check] validating CURRENT live nginx config only (no changes)…"
  $SUDO nginx -t
  exit $?
fi

# Back up whatever is live now (timestamped) to a dir OUTSIDE nginx's include
# globs, so the backup is never itself loaded as a second server block.
$SUDO mkdir -p "$BACKUP_DIR"
AVAIL_BAK="$BACKUP_DIR/sites-available.universal-agent-app.${TS}"
ENABLED_BAK="$BACKUP_DIR/sites-enabled.universal-agent-app.${TS}"
if [[ -e "$AVAIL" ]]; then
  $SUDO cp -a "$AVAIL" "$AVAIL_BAK"
  echo "backed up $AVAIL -> $AVAIL_BAK"
fi
if [[ -e "$ENABLED" || -L "$ENABLED" ]]; then
  $SUDO cp -aP "$ENABLED" "$ENABLED_BAK"
  echo "backed up $ENABLED -> $ENABLED_BAK"
fi

# Install canonical config and the standard sites-enabled -> sites-available
# symlink (replacing any stray real file that may be shadowing it).
$SUDO install -m 0644 "$SRC" "$AVAIL"
$SUDO ln -sfn "$AVAIL" "$ENABLED"

# Validate; roll back on any failure. sites-enabled is a symlink to
# sites-available, so restoring sites-available content is sufficient.
if ! $SUDO nginx -t; then
  echo "nginx -t FAILED — restoring previous sites-available + symlink." >&2
  [[ -e "$AVAIL_BAK" ]] && $SUDO cp -a "$AVAIL_BAK" "$AVAIL"
  $SUDO ln -sfn "$AVAIL" "$ENABLED"
  $SUDO nginx -t || echo "WARNING: restored config also fails nginx -t" >&2
  exit 1
fi

$SUDO systemctl reload nginx
echo "nginx reloaded."

# Verify the *running* config actually carries the briefs route.
if $SUDO nginx -T 2>/dev/null | grep -q 'location \^~ /briefs/'; then
  echo "active nginx config includes the /briefs/ route ✓"
else
  echo "WARNING: active nginx config does NOT show /briefs/ — investigate." >&2
fi

# Smoke the gateway directly: a bogus id should yield 404 from the gateway's
# own "Brief not found" handler (proves the route is alive). 000/502 = gateway down.
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
  http://127.0.0.1:8002/briefs/__installer_healthcheck__ || true)"
echo "gateway :8002 /briefs/ smoke -> HTTP ${code} (404 = healthy; 000/502 = gateway down)"
echo "DONE."
