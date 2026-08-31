#!/bin/bash
# Smart index update script for the maintainer.
# Automatically tracks the last successful run and fetches everything since then.
# Publishes knowledge/ as a GitHub Release asset (tag: knowledge) — not as git LFS.
#
# Usage:
#   ./update_index.sh              # Auto: fetches since last run (or last 1 day if first run)
#   ./update_index.sh 7            # Override: fetch last 7 days
#   ./update_index.sh 2024-01-01   # Override: fetch since a specific date
#   ./update_index.sh --reset      # Reset the last-run tracker
#   ./update_index.sh --publish-only  # Pack + upload current knowledge/ (no fetch; does not write .last_index_update)

set -euo pipefail
cd "$(dirname "$0")"

LAST_RUN_FILE=".last_index_update"
RELEASE_TAG="knowledge"
ASSET_NAME="knowledge.tar.gz"
LOCK_FILE="knowledge/.indexing_lock"

# Load credentials
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Handle --reset
if [[ "${1:-}" == "--reset" ]]; then
    rm -f "$LAST_RUN_FILE"
    echo "Last-run tracker reset. Next run will fetch last 1 day."
    exit 0
fi

publish_knowledge() {
    if ! command -v gh >/dev/null 2>&1; then
        echo "error: gh CLI not found. Install GitHub CLI and run: gh auth login" >&2
        exit 1
    fi
    if ! gh auth status >/dev/null 2>&1; then
        echo "error: gh is not authenticated. Run: gh auth login -h github.com" >&2
        exit 1
    fi

    echo "Checking knowledge/ for unsanitized customer data..."
    python3 scripts/sanitize_issues.py --check

    TOTAL=$(python3 -c "
import json
from pathlib import Path
root = Path('knowledge/issues-2024-2025')
n = 0
for p in root.glob('*/issues.json'):
    n += len(json.loads(p.read_text()))
print(n)
")
    MIN_ISSUES="${MIN_ISSUES:-10000}"
    if [ "$TOTAL" -lt "$MIN_ISSUES" ]; then
        echo "error: refusing to publish $TOTAL issues (floor is $MIN_ISSUES)." >&2
        echo "error: incremental merge likely started from an empty ibm-jira/issues.json." >&2
        echo "error: restore knowledge/ and re-run, or override with MIN_ISSUES=0" >&2
        exit 1
    fi

    STAGING=$(mktemp -d)
    TAR="$STAGING/$ASSET_NAME"
    trap 'rm -rf "$STAGING"' RETURN

    echo "Packing knowledge/ -> $ASSET_NAME ($TOTAL issues)"
    COPYFILE_DISABLE=1 tar -czf "$TAR" \
        -C knowledge \
        --exclude='.release_etag' \
        --exclude='.staging' \
        --exclude='.knowledge.tar.gz.partial' \
        --exclude='tmp' \
        .

    SIZE=$(du -h "$TAR" | awk '{print $1}')
    echo "Archive size: $SIZE"

    NOTES="Pre-built issue index: ${TOTAL} issues ($(date -u +%Y-%m-%d))"

    if gh release view "$RELEASE_TAG" >/dev/null 2>&1; then
        echo "Uploading $ASSET_NAME to existing release '$RELEASE_TAG' (replacing previous asset)"
        gh release upload "$RELEASE_TAG" "$TAR" --clobber
        gh release edit "$RELEASE_TAG" --notes "$NOTES"
    else
        echo "Creating release '$RELEASE_TAG'"
        gh release create "$RELEASE_TAG" "$TAR" \
            --title "Issue index" \
            --notes "$NOTES"
    fi

    echo "Published: https://github.com/pdhiran/ceph-issue-kb/releases/tag/$RELEASE_TAG"
}

if [[ "${1:-}" == "--publish-only" ]]; then
    mkdir -p knowledge
    echo $$ > "$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
    publish_knowledge
    touch .reload_trigger
    # Do not write .last_index_update — this path did not fetch, so the
    # since-cursor must stay put for the next incremental run.
    echo ""
    echo "=== Knowledge published (no re-index; .last_index_update unchanged) ==="
    exit 0
fi

# Determine the "since" date
if [[ -n "${1:-}" ]]; then
    # Explicit override provided
    ARG="$1"
    if [[ "$ARG" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        SINCE="$ARG"
    else
        SINCE=$(date -v-"${ARG}"d +%Y-%m-%d 2>/dev/null || date -d "${ARG} days ago" +%Y-%m-%d)
    fi
elif [[ -f "$LAST_RUN_FILE" ]]; then
    # Smart mode: fetch since last successful run
    SINCE=$(cat "$LAST_RUN_FILE")
    echo "(Last successful run: $SINCE)"
else
    # First run ever: fetch last 1 day
    SINCE=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "1 day ago" +%Y-%m-%d)
    echo "(First run — fetching last 1 day)"
fi

echo "=== Ceph Issue KB Index Update ==="
echo "Fetching issues updated since: $SINCE"
echo ""

mkdir -p knowledge
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# Run the indexer
python3 index_issues.py --config connectors.yaml --since "$SINCE" --verbose

# Signal MCP server to hot-reload (picked up within 5s by the trigger watcher)
touch .reload_trigger

publish_knowledge

# Only advance the since-cursor after a successful publish
date -v-1d +%Y-%m-%d > "$LAST_RUN_FILE" 2>/dev/null || date -d "1 day ago" +%Y-%m-%d > "$LAST_RUN_FILE"

echo ""
echo "=== Index updated and published ==="
