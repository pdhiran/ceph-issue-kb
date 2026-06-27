#!/bin/bash
# Smart index update script for the maintainer.
# Automatically tracks the last successful run and fetches everything since then.
#
# Usage:
#   ./update_index.sh              # Auto: fetches since last run (or last 1 day if first run)
#   ./update_index.sh 7            # Override: fetch last 7 days
#   ./update_index.sh 2024-01-01   # Override: fetch since a specific date
#   ./update_index.sh --reset      # Reset the last-run tracker

set -euo pipefail
cd "$(dirname "$0")"

LAST_RUN_FILE=".last_index_update"

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

# Save today's date as last successful run
date +%Y-%m-%d > "$LAST_RUN_FILE"

# Commit and push if there are changes
if [ -n "$(git status --porcelain knowledge/)" ]; then
    git add knowledge/
    git commit -m "Update issue index (since $SINCE)"
    git push origin main
    echo ""
    echo "=== Index updated and pushed ==="
else
    echo ""
    echo "=== No changes to index ==="
fi
