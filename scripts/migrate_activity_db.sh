#!/usr/bin/env bash
# One-time migration: copy activity/CSI tables from runtime_state.db to activity_state.db
# Safe to run multiple times (uses INSERT OR IGNORE).
set -euo pipefail

WORKSPACES="${1:-/opt/universal_agent/AGENT_RUN_WORKSPACES}"
RUNTIME_DB="$WORKSPACES/runtime_state.db"
ACTIVITY_DB="$WORKSPACES/activity_state.db"

if [[ ! -f "$RUNTIME_DB" ]]; then
    echo "No runtime_state.db found at $RUNTIME_DB — nothing to migrate."
    exit 0
fi

# Check if source tables exist
has_table() {
    sqlite3 "$RUNTIME_DB" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='$1' LIMIT 1;" 2>/dev/null | grep -q 1
}

if ! has_table "activity_events" && ! has_table "csi_specialist_loops"; then
    echo "No activity/CSI tables in runtime_state.db — nothing to migrate."
    exit 0
fi

echo "Migrating activity/CSI data from runtime_state.db → activity_state.db ..."

# Dump the relevant tables from runtime_state.db
TABLES=()
for t in activity_events activity_event_audit activity_event_stream dashboard_event_filter_presets csi_specialist_loops; do
    if has_table "$t"; then
        TABLES+=("$t")
    fi
done

if [[ ${#TABLES[@]} -eq 0 ]]; then
    echo "No tables to migrate."
    exit 0
fi

# Create activity_state.db with WAL mode
sqlite3 "$ACTIVITY_DB" "PRAGMA journal_mode=WAL;" 2>/dev/null || true

# Dump schema + data for each table and pipe into activity_state.db
for t in "${TABLES[@]}"; do
    count_before=$(sqlite3 "$RUNTIME_DB" "SELECT count(*) FROM $t;" 2>/dev/null || echo 0)
    echo "  $t: $count_before rows"
    # Dump schema (CREATE TABLE IF NOT EXISTS) + data (INSERT OR IGNORE)
    sqlite3 "$RUNTIME_DB" ".dump $t" 2>/dev/null | \
        sed 's/^CREATE TABLE /CREATE TABLE IF NOT EXISTS /g' | \
        sed 's/^INSERT INTO /INSERT OR IGNORE INTO /g' | \
        sqlite3 "$ACTIVITY_DB" 2>/dev/null || true
done

echo "Migration complete. Verify:"
for t in "${TABLES[@]}"; do
    count=$(sqlite3 "$ACTIVITY_DB" "SELECT count(*) FROM $t;" 2>/dev/null || echo "?")
    echo "  $t: $count rows in activity_state.db"
done

echo ""
echo "Old tables remain in runtime_state.db (harmless). They can be dropped manually:"
echo "  sqlite3 $RUNTIME_DB 'DROP TABLE IF EXISTS activity_events; DROP TABLE IF EXISTS activity_event_audit; DROP TABLE IF EXISTS activity_event_stream; DROP TABLE IF EXISTS dashboard_event_filter_presets; DROP TABLE IF EXISTS csi_specialist_loops;'"
