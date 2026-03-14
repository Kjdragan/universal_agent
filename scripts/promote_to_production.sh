#!/usr/bin/env bash
# promote_to_production.sh — safely trigger the Promote Validated Develop To Main workflow.
#
# Always fetches the latest origin/develop SHA immediately before dispatching so the
# workflow never rejects due to a stale SHA (which fails if develop moved between
# your local fetch and the gh workflow run call).
#
# Usage:
#   ./scripts/promote_to_production.sh
#
# Requires: gh CLI authenticated, git remote named origin pointing to the repo.

set -euo pipefail

WORKFLOW_NAME="Promote Validated Develop To Main"

echo "--> Fetching latest origin/develop..."
git fetch --no-tags origin develop

DEVELOP_SHA=$(git rev-parse origin/develop)
echo "--> Current origin/develop SHA: ${DEVELOP_SHA}"
echo "--> Dispatching '${WORKFLOW_NAME}' with develop_sha=${DEVELOP_SHA}..."

gh workflow run "${WORKFLOW_NAME}" --field develop_sha="${DEVELOP_SHA}"

echo "--> Workflow dispatched! Watching for completion..."
sleep 5
RUN_ID=$(gh run list --workflow="promote-develop-to-main.yml" --limit 1 --json databaseId --jq '.[0].databaseId')
echo "--> Run ID: ${RUN_ID}"
gh run watch "${RUN_ID}" --exit-status
