#!/usr/bin/env bash
# Description: Cutover script for Phase 3A Pipeline Simplification
# Must be run as root or with sudo privileges on the VPS.

set -euo pipefail

echo "=========================================================="
echo " Starting Cutover to Single Environment (Phase 3A)"
echo "=========================================================="

# 1. Stop and disable staging systemd units
echo "--> Checking and disabling staging systemd units..."

# Define the staging services
STAGING_SERVICES=(
  "universal-agent-staging-gateway"
  "universal-agent-staging-api"
  "universal-agent-staging-webui"
  "universal-agent-staging-telegram"
)

for svc in "${STAGING_SERVICES[@]}"; do
  if systemctl list-unit-files "$svc.service" > /dev/null 2>&1 || systemctl is-active --quiet "$svc"; then
    echo "    Stopping and disabling $svc..."
    systemctl stop "$svc" || true
    systemctl disable "$svc" || true
  else
    echo "    Service $svc not found or already disabled."
  fi
done

# Assuming staging workers are also present
STAGING_WORKERS=$(systemctl list-units --type=service | grep "universal-agent-staging-vp-worker" | awk '{print $1}' || true)
if [ -n "$STAGING_WORKERS" ]; then
    for worker in $STAGING_WORKERS; do
        echo "    Stopping and disabling staging worker: $worker"
        systemctl stop "$worker" || true
        systemctl disable "$worker" || true
    done
else
    echo "    No staging VP workers found."
fi

# 2. Archive /opt/universal-agent-staging
STAGING_DIR="/opt/universal-agent-staging"
TODAY=$(date +%Y%m%d)
ARCHIVE_DIR="/opt/universal-agent-staging.archived.$TODAY"

echo "--> Processing staging directory archive..."
if [ -d "$STAGING_DIR" ]; then
  if [ -d "$ARCHIVE_DIR" ]; then
    echo "    Archive directory $ARCHIVE_DIR already exists. Moving existing contents inside..."
    mv "$STAGING_DIR"/* "$ARCHIVE_DIR"/ 2>/dev/null || true
    rmdir "$STAGING_DIR" 2>/dev/null || true
  else
    echo "    Moving $STAGING_DIR to $ARCHIVE_DIR..."
    mv "$STAGING_DIR" "$ARCHIVE_DIR"
  fi
  echo "    Archived successfully."
else
  echo "    Staging directory $STAGING_DIR does not exist. It may have already been archived."
fi

# 3. Verify /opt/universal_agent is healthy
PROD_DIR="/opt/universal_agent"
echo "--> Verifying production directory state..."

if [ -d "$PROD_DIR/.git" ]; then
  echo "    [OK] Production Git tracking directory exists."
else
  echo "    [WARN] Production directory $PROD_DIR is missing .git tracking!"
fi

PROD_SERVICES=(
  "universal-agent-gateway"
  "universal-agent-api"
  "universal-agent-webui"
  "universal-agent-telegram"
)

all_healthy=true
for svc in "${PROD_SERVICES[@]}"; do
  if systemctl is-active --quiet "$svc"; then
    echo "    [OK] $svc is running."
  else
    echo "    [WARN] $svc is NOT active!"
    all_healthy=false
  fi
done

echo "=========================================================="
echo " Cutover Complete."
if [ "$all_healthy" = true ]; then
  echo " Production environment appears healthy."
else
  echo " Please check the warning messages above for production."
fi
echo "=========================================================="
