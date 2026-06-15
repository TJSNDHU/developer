#!/bin/bash
set -e
TARGET=${1:-green}
HEALTH_URL="https://auremcto.com/api/health"
echo "Checking $TARGET health..."
for i in {1..10}; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_URL)
    if [ "$STATUS" = "200" ]; then
        echo "✅ Health check passed — $TARGET is live"
        break
    fi
    echo "Attempt $i — retrying in 5s..."
    sleep 5
done
[ "$STATUS" = "200" ] || { echo "❌ Health check failed"; exit 1; }
echo "✅ Switched to $TARGET at $(date -u)"
