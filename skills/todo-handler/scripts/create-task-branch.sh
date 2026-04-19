#!/usr/bin/env bash
# create-task-branch.sh <branch> [submodule-path ...]
#
# Fetches origin/main (falling back to origin/master) and creates <branch>
# from that remote ref in the parent repo. For each <submodule-path> given,
# does the same inside that submodule using the same branch name.
#
# Usage:
#   skills/todo-handler/scripts/create-task-branch.sh my-feature
#   skills/todo-handler/scripts/create-task-branch.sh my-feature WLED libs/foo

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <branch> [submodule-path ...]" >&2
  exit 1
fi

BRANCH="$1"
shift
SUBMODULES=("$@")

# Determine base branch (main preferred, master fallback)
base_ref() {
  local dir="${1:-.}"
  if git -C "$dir" rev-parse --verify origin/main >/dev/null 2>&1; then
    echo "main"
  elif git -C "$dir" rev-parse --verify origin/master >/dev/null 2>&1; then
    echo "master"
  else
    echo "Error: neither origin/main nor origin/master found in $dir" >&2
    exit 1
  fi
}

# Parent repo
BASE=$(base_ref .)
echo "Fetching origin/$BASE in parent repo..."
git fetch origin "$BASE"
echo "Creating branch '$BRANCH' from origin/$BASE..."
git checkout -b "$BRANCH" "origin/$BASE"
echo "Parent repo: branch '$BRANCH' created."

# Submodules
for SUB in "${SUBMODULES[@]}"; do
  if [[ ! -d "$SUB" ]]; then
    echo "Warning: submodule path '$SUB' not found, skipping." >&2
    continue
  fi
  BASE_SUB=$(base_ref "$SUB")
  echo "Fetching origin/$BASE_SUB in $SUB..."
  git -C "$SUB" fetch origin "$BASE_SUB"
  echo "Creating branch '$BRANCH' from origin/$BASE_SUB in $SUB..."
  git -C "$SUB" checkout -b "$BRANCH" "origin/$BASE_SUB"
  echo "$SUB: branch '$BRANCH' created."
done

echo "Done."
