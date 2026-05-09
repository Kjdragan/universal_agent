#!/usr/bin/env bash
# Install / refresh the hackernews-pp-cli binary on the VPS.
# Pin to a specific GitHub release tag, NOT "latest" — reproducibility per the
# Phase 1 plan (docs/integrations/hackernews_phase1_plan.md § 4 P1.1).
#
# SHA256 is intentionally a placeholder for the first install.  After the first
# successful install on the VPS, capture the real digest with `sha256sum` and
# replace the placeholder so subsequent installs are integrity-checked.

set -euo pipefail

HN_CLI_VERSION="${HN_CLI_VERSION:-hackernews-current}"
# TBD — capture at first install, then commit the real SHA256 here.
HN_CLI_SHA256="${HN_CLI_SHA256:-<TBD — capture at first install>}"
HN_CLI_URL="${HN_CLI_URL:-https://github.com/mvanhorn/printing-press-library/releases/download/${HN_CLI_VERSION}/hackernews-pp-cli-linux-amd64.tar.gz}"

INSTALL_DIR="/opt/universal_agent/bin"
TARGET="${INSTALL_DIR}/hackernews-pp-cli"
XDG_BASE="/opt/universal_agent/var/hackernews"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

mkdir -p "$INSTALL_DIR"
echo "Fetching ${HN_CLI_URL}"
curl -sSL "$HN_CLI_URL" -o "$TMPDIR/cli.tar.gz"

if [[ "$HN_CLI_SHA256" == "<TBD — capture at first install>" ]]; then
  echo "WARNING: HN_CLI_SHA256 is a placeholder. Capturing actual digest:"
  sha256sum "$TMPDIR/cli.tar.gz"
  echo "Update scripts/install_hackernews_cli.sh with the value above."
else
  echo "${HN_CLI_SHA256}  ${TMPDIR}/cli.tar.gz" | sha256sum -c -
fi

tar -xzf "$TMPDIR/cli.tar.gz" -C "$TMPDIR"
install -m 0755 "$TMPDIR/hackernews-pp-cli" "$TARGET"

mkdir -p "${XDG_BASE}/config/hackernews-pp-cli"
mkdir -p "${XDG_BASE}/data/hackernews-pp-cli"

"$TARGET" --version
XDG_CONFIG_HOME="${XDG_BASE}/config" \
XDG_DATA_HOME="${XDG_BASE}/data" \
"$TARGET" doctor

echo "Installed hackernews-pp-cli ${HN_CLI_VERSION} at ${TARGET}"
