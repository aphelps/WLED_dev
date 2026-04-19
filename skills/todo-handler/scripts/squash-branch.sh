#!/usr/bin/env bash
# squash-branch.sh <base-ref> <commit-message>
#
# Squashes all commits on the current branch above <base-ref> into a single
# commit with <commit-message>. Uses git reset --soft to preserve the working
# tree (gitignored files like todo.md are untouched).
#
# Usage:
#   skills/todo-handler/scripts/squash-branch.sh origin/main "My commit message"

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <base-ref> <commit-message>" >&2
  exit 1
fi

BASE="$1"
MSG="$2"

# Verify base ref exists
if ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
  echo "Error: base ref '$BASE' not found. Run 'git fetch origin' first." >&2
  exit 1
fi

COMMITS=$(git rev-list --count "$BASE"..HEAD)
if [[ "$COMMITS" -eq 0 ]]; then
  echo "Nothing to squash: no commits above $BASE." >&2
  exit 1
fi

echo "Squashing $COMMITS commit(s) above $BASE into one..."
git reset --soft "$BASE"
git commit -m "$MSG"
echo "Done. New HEAD: $(git rev-parse --short HEAD)"
