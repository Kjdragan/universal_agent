#!/usr/bin/env bash
# Install / refresh the hackernews-pp-cli binary on the VPS.
# Pin to a specific GitHub release tag, NOT "latest" — reproducibility per the
# Phase 1 plan (docs/integrations/hackernews_phase1_plan.md § 4 P1.1).
#
# The release publishes raw binaries (one per OS/arch), not gzipped tarballs.
# We download the linux-amd64 binary directly and verify its SHA256 against
# the value captured at pinning time. The published SHA256 below comes from
# the release asset metadata for the `hackernews-current` tag — bump both
# HN_CLI_VERSION and HN_CLI_SHA256 together when upgrading to a newer build.
#
# INSTALL TARGET: $HOME/.local/bin/hackernews-pp-cli
#
# Why $HOME/.local/bin/ and not /opt/universal_agent/bin/:
#   /opt/universal_agent/ is the production checkout — synced to origin/main
#   on every deploy and used as an agent scratch directory. Untracked dirs
#   inside the repo tree (like /opt/universal_agent/bin/) are vulnerable to
#   `git clean`, autonomous-agent cleanup, or operator wipes. We saw exactly
#   this fail mode on 2026-05-09 (issue #179) where the binary disappeared
#   between cron ticks and produced ~30 [ERROR] emails before being noticed.
#
#   $HOME/.local/bin/ lives outside the repo tree, survives all of those
#   failure modes, and is the established pattern in this codebase
#   (see deploy.yml goplaces install).
#
# IDEMPOTENCY: This script is safe to run on every deploy. It always
# downloads + verifies + installs, but the cost is small (~13 MB, single
# SHA-pinned download). If you want to skip when already installed, the
# deploy.yml caller can guard with `if [ ! -x "$HOME/.local/bin/hackernews-pp-cli" ]`.

set -euo pipefail

HN_CLI_VERSION="${HN_CLI_VERSION:-hackernews-current}"
# Captured 2026-05-09 from `gh release view hackernews-current --json assets`
# for asset `hackernews-pp-cli-linux-amd64`.
HN_CLI_SHA256="${HN_CLI_SHA256:-65b1d25d12a0ac18abb174c7c016a7a861a8409c395d9edc00dabf5783954cd4}"
HN_CLI_URL="${HN_CLI_URL:-https://github.com/mvanhorn/printing-press-library/releases/download/${HN_CLI_VERSION}/hackernews-pp-cli-linux-amd64}"

INSTALL_DIR="${HOME}/.local/bin"
TARGET="${INSTALL_DIR}/hackernews-pp-cli"
# hackernews-pp-cli v1.0.0 derives its config + SQLite paths from $HOME and
# does NOT honor XDG_CONFIG_HOME / XDG_DATA_HOME (verified empirically).
# CLI_HOME is the override that makes `~/.config` and `~/.local/share`
# resolve into the project tree at /opt/universal_agent/var/hackernews.
CLI_HOME="/opt/universal_agent/var/hackernews"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

mkdir -p "$INSTALL_DIR"
echo "Fetching ${HN_CLI_URL}"
# Prefer curl, fall back to wget. Some VPS shell environments have a curl
# CA-bundle quirk that breaks SHA-pinned downloads; wget routes around it.
if command -v curl >/dev/null 2>&1 && curl -fsSL "$HN_CLI_URL" -o "$TMPDIR/hackernews-pp-cli" 2>/dev/null; then
  :
elif command -v wget >/dev/null 2>&1; then
  wget --quiet -O "$TMPDIR/hackernews-pp-cli" "$HN_CLI_URL"
else
  echo "ERROR: neither curl nor wget is installed" >&2
  exit 1
fi

echo "${HN_CLI_SHA256}  ${TMPDIR}/hackernews-pp-cli" | sha256sum -c -

install -m 0755 "$TMPDIR/hackernews-pp-cli" "$TARGET"

mkdir -p "${CLI_HOME}/.config/hackernews-pp-cli"
mkdir -p "${CLI_HOME}/.local/share/hackernews-pp-cli"

"$TARGET" --version
HOME="${CLI_HOME}" "$TARGET" doctor

echo "Installed hackernews-pp-cli ${HN_CLI_VERSION} at ${TARGET}"
