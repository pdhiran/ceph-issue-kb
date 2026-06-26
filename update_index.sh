#!/bin/bash
# Daily index update script for the maintainer.
# Fetches issues updated since yesterday, rebuilds the index, and commits.
#
# Usage:
#   ./update_index.sh              # Fetch last 1 day of updates
#   ./update_index.sh 7            # Fetch last 7 days of updates
#   ./update_index.sh 2024-01-01   # Fetch since a specific date

set -euo pipefail
cd "$(dirname "$0")"

# Load credentials
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Determine the "since" date
DAYS="${1:-1}"
if [[ "$DAYS" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    SINCE="$DAYS"
else
    SINCE=$(date -v-"${DAYS}"d +%Y-%m-%d 2>/dev/null || date -d "${DAYS} days ago" +%Y-%m-%d)
fi

echo "=== Ceph Issue KB Index Update ==="
echo "Fetching issues updated since: $SINCE"
echo ""

# Run the indexer
python3 index_issues.py --config connectors.yaml --since "$SINCE" --verbose

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
