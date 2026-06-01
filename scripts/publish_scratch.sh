#!/usr/bin/env bash
# publish_scratch.sh — Publish an HTML file to the tailnet HTML scratchpad and print its link.
#
# The scratchpad is a `tailscale serve` path-mount on the VPS that exposes
# /home/ua/ua_scratch/<slug>/<file>.html at:
#   https://uaonvps.taildcc090.ts.net/scratch/<slug>/<file>.html
# It is reachable ONLY from the operator's own tailnet devices (desktop/phone/tablet),
# never the public internet — tailnet membership is the auth boundary.
#
# This is how a terminal-only operator gets a real, clickable, fully-rendered HTML
# report (styled, with diagrams) instead of a markdown file or a link-stripped email/PDF.
#
# Canonical reference: docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md § 6
# Mirror (project_docs tree): project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md § 1.6
#
# USAGE
#   scripts/publish_scratch.sh <html-file> [slug]
#       Publish <html-file>. Optional readable [slug] names the subdir
#       (default: a random unguessable hex slug). Prints the tailnet URL.
#
#   scripts/publish_scratch.sh --init
#       One-time (idempotent) setup of the /scratch serve mapping on the VPS.
#       Safe to re-run; never touches the other serve mappings.
#
#   scripts/publish_scratch.sh --status
#       Show the current `tailscale serve` mappings (verify /scratch is live).
#
# WHERE IT RUNS
#   - On the VPS (as `ua`): writes directly to /home/ua/ua_scratch.
#   - On the desktop / anywhere on the tailnet: copies over `ssh ua@uaonvps`.
#   Detection is automatic — you don't pass a flag.
set -euo pipefail

SCRATCH_ROOT="/home/ua/ua_scratch"
VPS_HOST="ua@uaonvps"
TS_HOST="uaonvps.taildcc090.ts.net"

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

# True when /home/ua/ua_scratch is a directory we can write to (i.e. we're on the VPS as ua).
on_vps() { [[ -d "$SCRATCH_ROOT" && -w "$SCRATCH_ROOT" ]]; }

run_remote() { ssh -o ConnectTimeout=10 "$VPS_HOST" "$@"; }

cmd_status() {
  if on_vps; then
    sudo tailscale serve status 2>/dev/null || tailscale serve status
  else
    run_remote 'sudo tailscale serve status 2>/dev/null || tailscale serve status'
  fi
}

cmd_init() {
  # Configure the /scratch path-mount. Idempotent: tailscale serve replaces an
  # existing mapping with an identical one, and leaves all other mappings intact.
  local setup='mkdir -p '"$SCRATCH_ROOT"' && sudo tailscale serve --bg --set-path /scratch '"$SCRATCH_ROOT"
  if on_vps; then
    bash -c "$setup"
  else
    run_remote "$setup"
  fi
  err "Scratchpad serve mapping ensured. Verifying it did not disturb others:"
  cmd_status
}

cmd_publish() {
  local src="$1"
  local slug="${2:-}"
  [[ -f "$src" ]] || die "no such file: $src"

  # Default to a random, unguessable slug. Good hygiene (the URL is unlisted),
  # though tailnet membership — not the slug — is the real security boundary.
  if [[ -z "$slug" ]]; then
    slug="$(openssl rand -hex 6 2>/dev/null || head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  # Keep the slug filesystem/URL-safe.
  [[ "$slug" =~ ^[A-Za-z0-9._-]+$ ]] || die "slug must be [A-Za-z0-9._-]+ (got: $slug)"

  local fname dest_dir
  fname="$(basename "$src")"
  dest_dir="$SCRATCH_ROOT/$slug"

  if on_vps; then
    mkdir -p "$dest_dir"
    install -m 0644 "$src" "$dest_dir/$fname"
    chmod 0755 "$dest_dir"
  else
    run_remote "mkdir -p '$dest_dir' && chmod 0755 '$dest_dir'"
    scp -q -o ConnectTimeout=10 "$src" "$VPS_HOST:$dest_dir/$fname"
    run_remote "chmod 0644 '$dest_dir/$fname'"
  fi

  local url="https://$TS_HOST/scratch/$slug/$fname"
  err "Published (tailnet-only, private to your devices):"
  # The URL goes to stdout alone so callers can capture it: URL=$(publish_scratch.sh f.html)
  printf '%s\n' "$url"
}

main() {
  case "${1:-}" in
    --init)    cmd_init ;;
    --status)  cmd_status ;;
    -h|--help|"") sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//' ;;
    --*)       die "unknown flag: $1" ;;
    *)         cmd_publish "$@" ;;
  esac
}

main "$@"
