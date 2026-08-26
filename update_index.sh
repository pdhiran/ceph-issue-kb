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
#   ./update_index.sh --publish-only  # Pack + upload the current knowledge/ (no re-index)

set -euo pipefail
cd "$(dirname "$0")"

LAST_RUN_FILE=".last_index_update"
RELEASE_TAG="knowledge"
ASSET_NAME="knowledge.tar.gz"

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

    STAGING=$(mktemp -d)
    TAR="$STAGING/$ASSET_NAME"
    trap 'rm -rf "$STAGING"' RETURN

    echo "Packing knowledge/ -> $ASSET_NAME"
    COPYFILE_DISABLE=1 tar -czf "$TAR" \
        -C knowledge \
        --exclude='.release_etag' \
        --exclude='.staging' \
        --exclude='.knowledge.tar.gz.partial' \
        --exclude='tmp' \
        .

    SIZE=$(du -h "$TAR" | awk '{print $1}')
    echo "Archive size: $SIZE"

    NOTES="Pre-built issue index ($(date -u +%Y-%m-%d))"
    if [ -f knowledge/issues-2024-2025/metadata.json ]; then
        TOTAL=$(python3 -c "import json; print(json.load(open('knowledge/issues-2024-2025/metadata.json'))['total_issues'])" 2>/dev/null || true)
        if [ -n "${TOTAL:-}" ]; then
            NOTES="Pre-built issue index: ${TOTAL} issues ($(date -u +%Y-%m-%d))"
        fi
    fi

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
    publish_knowledge
    touch .reload_trigger
    echo ""
    echo "=== Knowledge published (no re-index) ==="
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

# Run the indexer
python3 index_issues.py --config connectors.yaml --since "$SINCE" --verbose

# Signal MCP server to hot-reload (picked up within 5s by the trigger watcher)
touch .reload_trigger

# Save yesterday's date for 1-day overlap buffer (prevents edge-case misses)
date -v-1d +%Y-%m-%d > "$LAST_RUN_FILE" 2>/dev/null || date -d "1 day ago" +%Y-%m-%d > "$LAST_RUN_FILE"

publish_knowledge

echo ""
echo "=== Index updated and published ==="
