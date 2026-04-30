#!/usr/bin/env bash
# bootstrap_link_auth.sh — one-time local bootstrap for Stripe Link CLI auth.
#
# Run this on a workstation (NOT the VPS) where you can interactively complete
# the Link device-auth flow. The script:
#   1. Verifies `npx` is available.
#   2. Runs `link-cli auth login` interactively, identifying as "Universal
#      Agent (production)" so the connection shows up clearly in your Link app.
#   3. Detects the auth-blob file Link CLI wrote to disk.
#   4. Prints a base64 of the blob plus the destination path, so you can paste
#      both into Infisical as LINK_AUTH_BLOB and UA_LINK_AUTH_BLOB_PATH.
#
# This script NEVER writes a .env file. Production secrets live only in
# Infisical. The auth blob you generate here is paired-locked to the
# Link account that approves it on the device-auth flow — only that human can
# approve future spend requests.

set -euo pipefail

CLIENT_NAME="${UA_LINK_CLIENT_NAME:-Universal Agent (production)}"

if ! command -v npx >/dev/null 2>&1; then
  echo "❌ npx is required. Install Node.js first." >&2
  exit 1
fi

MARKER="$(mktemp)"
trap 'rm -f "$MARKER"' EXIT
touch "$MARKER"

echo "▶ Running link-cli auth login (you will see a verification URL + phrase)..."
echo "▶ Visit the URL on your phone, log in to Link, enter the phrase to approve."
echo
npx -y @stripe/link-cli auth login --client-name "$CLIENT_NAME"
echo

CANDIDATE_DIRS=(
  "$HOME/.config"
  "$HOME/.local/share"
  "$HOME/Library/Preferences"
  "$HOME/Library/Application Support"
)

FOUND=()
for dir in "${CANDIDATE_DIRS[@]}"; do
  [ -d "$dir" ] || continue
  while IFS= read -r f; do
    FOUND+=("$f")
  done < <(find "$dir" -type f -newer "$MARKER" 2>/dev/null \
            | grep -iE 'link|stripe' || true)
done

cat <<EOF

============================================================
✅ Link CLI authentication complete.
============================================================
EOF

if [ ${#FOUND[@]} -eq 0 ]; then
  cat >&2 <<EOF

⚠️  Could not auto-detect the auth blob path. Locate it manually with:

    find ~/.config ~/.local/share ~/Library -newer "$MARKER" 2>/dev/null

Then base64 the file and paste it into Infisical as LINK_AUTH_BLOB,
and set UA_LINK_AUTH_BLOB_PATH to the file path (use \$HOME, not the
absolute /home/...).
EOF
  exit 0
fi

BLOB_PATH="${FOUND[0]}"
PORTABLE_PATH="${BLOB_PATH/$HOME/\$HOME}"

cat <<EOF

Detected auth-blob file:
  $BLOB_PATH

Paste these two values into Infisical (production environment):

  Key:   UA_LINK_AUTH_BLOB_PATH
  Value: $PORTABLE_PATH

  Key:   LINK_AUTH_BLOB
  Value: (base64 below — single line, no trailing newline)

----- BEGIN LINK_AUTH_BLOB -----
EOF

if command -v base64 >/dev/null 2>&1; then
  base64 -w0 "$BLOB_PATH" 2>/dev/null || base64 "$BLOB_PATH" | tr -d '\n'
  echo
else
  echo "⚠️  base64 not available; copy the file contents manually." >&2
fi

cat <<EOF
----- END LINK_AUTH_BLOB -----

After both values are in Infisical, restart the VPS service. The bridge will
restore the auth blob to the configured path on import. Verify with:

  curl -s https://app.clearspringcg.com/_ops/link/health

(Endpoint comes online in Phase 2b. For now, the startup logs will show
the health-probe result.)
EOF

if [ ${#FOUND[@]} -gt 1 ]; then
  echo
  echo "ℹ️  Other recently-written files (likely auxiliary, not auth):"
  for ((i=1; i<${#FOUND[@]}; i++)); do
    echo "    ${FOUND[$i]}"
  done
fi
