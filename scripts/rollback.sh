#!/bin/bash
set -e
echo "🚨 ROLLBACK at $(date -u)"
PREV=$(git log --oneline -2 | tail -1 | awk '{print $1}')
echo "Previous stable commit: $PREV"
echo "Run: Emergent Dashboard → Manage Deployments → Previous Deploy"
echo "Or contact support with this commit: $PREV"
