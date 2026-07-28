#!/bin/bash
# scripts/check-safe-to-delete.sh
# ─────────────────────────────────────────────────────────────────────
# MANDATORY DEPENDENCY-CHECK GATE (Iter 331)
# No file gets on an "approved for deletion" list without this script's
# FULL output pasted alongside. Verbal "checked, safe hai" = rejected.
#
# Why this exists: tool_executor.py was deleted 3× as "dead code" while
# being LAZY-imported (import inside a function body) — top-level grep
# and IDE "find usages" missed it. Sections 2 & 3 below catch exactly
# that class: lazy/dynamic imports and string-keyed routing tables.
#
# Usage: ./scripts/check-safe-to-delete.sh path/to/file.py
# ─────────────────────────────────────────────────────────────────────

FILE=$1
if [ -z "$FILE" ]; then
  echo "Usage: $0 path/to/file.py"; exit 2
fi
MODULE_NAME=$(basename "$FILE" | sed 's/\.[^.]*$//')
EXCL='--exclude-dir=node_modules --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.vite --exclude-dir=build --exclude-dir=dist'

cd "$(dirname "$0")/.." || exit 2

echo "=== Checking: $FILE (module: $MODULE_NAME) ==="
echo ""

echo "--- 1. Direct imports (top-level) ---"
grep -rn $EXCL "import $MODULE_NAME\|from.*$MODULE_NAME import\|from .$MODULE_NAME\|require.*$MODULE_NAME\|from ['\"].*$MODULE_NAME" \
  --include="*.py" --include="*.jsx" --include="*.js" --include="*.ts" --include="*.tsx" . | grep -v "$FILE"

echo ""
echo "--- 2. LAZY / dynamic imports (this is what got missed 3x) ---"
grep -rn $EXCL "import_module\|__import__\|importlib" --include="*.py" . | grep -i "$MODULE_NAME"
grep -rn $EXCL "$MODULE_NAME" --include="*.py" --include="*.jsx" --include="*.js" --include="*.ts" --include="*.tsx" . | grep -v "$FILE"

echo ""
echo "--- 3. String references (routing tables, config, test fixtures) ---"
grep -rn $EXCL "\"$MODULE_NAME\"\|'$MODULE_NAME'" \
  --include="*.py" --include="*.jsx" --include="*.js" --include="*.json" --include="*.yml" --include="*.yaml" . | grep -v "$FILE"

echo ""
echo "--- 4. Test specs (backend pytest + frontend vitest/playwright) that reference this file ---"
grep -rln $EXCL "$MODULE_NAME" backend/tests/ frontend/tests/ frontend/src/components/__tests__/ frontend/src/__tests__/ 2>/dev/null

echo ""
echo "=== VERDICT ==="
COUNT=$(grep -rl $EXCL "$MODULE_NAME" --include="*.py" --include="*.jsx" --include="*.js" --include="*.ts" --include="*.tsx" --include="*.json" --include="*.yml" . 2>/dev/null | grep -v "$FILE" | wc -l)
if [ "$COUNT" -eq 0 ]; then
  echo "✅ ZERO references found outside the file itself. Safe to delete"
  echo "   (still quarantine first — see docs/DELETE_GATE.md Layer 3)."
else
  echo "❌ $COUNT file(s) still reference this. DO NOT DELETE — investigate above."
fi
