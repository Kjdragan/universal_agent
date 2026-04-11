#!/usr/bin/env bash
# promote_to_production.sh — safely fast-forward production to the latest validated develop SHA.
#
# Always fetches the latest origin/develop SHA immediately before pushing so
# production release uses the exact current integration commit.
#
# Usage:
#   ./scripts/promote_to_production.sh
#
# Requires: gh CLI authenticated, git remote named origin pointing to the repo.

set -euo pipefail

WORKFLOW_FILE="deploy.yml"

echo "--> Fetching latest origin/develop and origin/main..."
git fetch --no-tags origin develop main

DEVELOP_SHA=$(git rev-parse origin/develop)
MAIN_SHA=$(git rev-parse origin/main)
echo "--> Current origin/develop SHA: ${DEVELOP_SHA}"
echo "--> Current origin/main SHA: ${MAIN_SHA}"
echo "--> Fast-forwarding main to ${DEVELOP_SHA}..."

git merge-base --is-ancestor "${MAIN_SHA}" "${DEVELOP_SHA}" || {
  echo "ERROR: origin/main is not an ancestor of origin/develop; refusing non-fast-forward production promotion." >&2
  exit 1
}

git push origin "${DEVELOP_SHA}:refs/heads/main"

echo "--> Main updated. Watching Deploy workflow..."
sleep 5
RUN_ID=$(gh run list --workflow="${WORKFLOW_FILE}" --branch main --limit 1 --json databaseId --jq '.[0].databaseId')
echo "--> Run ID: ${RUN_ID}"
gh run watch "${RUN_ID}" --exit-status
