#!/usr/bin/env bash
# vault_probe.sh — auth-or-die Infisical queries. NEVER prints secret values.
#
# Why this exists (SDF case 030, third occurrence, 2026-07-26): a bare
# `infisical secrets` in an unauthenticated shell returns nothing useful, and a
# pipeline reading that emptiness concluded "no relevant secrets" — a confident
# false negative that nearly gated a build behind a provisioning act the vault
# had already satisfied. Auth state is part of an instrument's authority: this
# wrapper either answers WITH authority (machine-identity universal-auth, the
# same bootstrap daily_brief_cron.sh uses in production) or refuses LOUDLY.
# A negative existence claim is only valid from exit code 0/1 — never from 3.
#
# Usage:
#   vault_probe.sh has NAME [NAME...]   per-name "present"/"absent" (names only)
#   vault_probe.sh list [EGREP]         matching secret NAMES (never values)
# Exit: 0 = authenticated, all `has` names present (or list ok)
#       1 = authenticated, at least one name absent (a VALID negative)
#       2 = usage error
#       3 = UNAUTHENTICATED — no negative claim from this run is valid
set -euo pipefail

die_unauth() {
  echo "vault-probe: UNAUTHENTICATED — $1" >&2
  echo "vault-probe: negative claims from this run are INVALID (the instrument has no authority here)." >&2
  exit 3
}

usage() { echo "usage: vault_probe.sh has NAME [NAME...] | list [EGREP]" >&2; exit 2; }

CMD="${1:-}"; shift || true
case "$CMD" in has|list) ;; *) usage ;; esac
[ "$CMD" = "has" ] && [ "$#" -lt 1 ] && usage

# --- machine-identity bootstrap (same discovery order everywhere) ---
CREDS="${UA_ENV_FILE:-}"
if [ -z "$CREDS" ]; then
  _REPO="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)"
  for c in /opt/universal_agent/.env "$_REPO/.env"; do
    [ -r "$c" ] && CREDS="$c" && break
  done
fi
if [ -n "$CREDS" ] && [ -r "$CREDS" ]; then
  set -a; . "$CREDS" >/dev/null 2>&1; set +a
fi

command -v infisical >/dev/null 2>&1 || die_unauth "infisical CLI not on PATH"
[ -n "${INFISICAL_CLIENT_ID:-}" ] && [ -n "${INFISICAL_CLIENT_SECRET:-}" ] \
  || die_unauth "no machine identity (INFISICAL_CLIENT_ID/SECRET) in env or env file"

ENVSLOT="${INFISICAL_ENVIRONMENT:-production}"
PID="${INFISICAL_PROJECT_ID:-9970e5b7-d48a-4ed8-a8af-43e923e67572}"

TOK="$(infisical login --method=universal-auth \
        --client-id="$INFISICAL_CLIENT_ID" \
        --client-secret="$INFISICAL_CLIENT_SECRET" --plain --silent)" \
  || die_unauth "universal-auth login failed"

# Values enter this variable only; nothing below ever prints past the '='.
NAMES="$(INFISICAL_TOKEN="$TOK" infisical export --env="$ENVSLOT" --projectId="$PID" \
           --format=dotenv 2>/dev/null \
         | awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/{print $1}')" \
  || die_unauth "secret listing failed"
# An empty listing is indistinguishable from a scope/permission problem, and
# this vault is never empty — refuse rather than certify absence.
[ -n "$NAMES" ] || die_unauth "listing returned empty — refusing to certify absence"

case "$CMD" in
  list)
    PATTERN="${1:-.}"
    printf '%s\n' "$NAMES" | grep -E -- "$PATTERN" || true
    ;;
  has)
    rc=0
    for name in "$@"; do
      if printf '%s\n' "$NAMES" | grep -qxF -- "$name"; then
        echo "$name present"
      else
        echo "$name absent"
        rc=1
      fi
    done
    exit "$rc"
    ;;
esac
