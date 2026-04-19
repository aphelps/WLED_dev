#!/usr/bin/env bash
# open-pr.sh --title <title> --head <branch> [--base <base>] [--body <text>|--body-file <file>] [--repo <owner/repo>]
#
# Non-interactive wrapper around 'gh pr create'. All arguments are passed
# explicitly so no editor is opened. Prints the resulting PR URL.
#
# Usage:
#   skills/todo-handler/scripts/open-pr.sh \
#     --title "My PR" \
#     --head my-feature \
#     --body "## Summary\n- thing 1"
#
#   skills/todo-handler/scripts/open-pr.sh \
#     --title "My PR" \
#     --head my-feature \
#     --body-file /tmp/pr-body.md \
#     --repo owner/repo

set -euo pipefail

TITLE=""
HEAD=""
BASE="main"
BODY=""
BODY_FILE=""
REPO_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)     TITLE="$2";     shift 2 ;;
    --head)      HEAD="$2";      shift 2 ;;
    --base)      BASE="$2";      shift 2 ;;
    --body)      BODY="$2";      shift 2 ;;
    --body-file) BODY_FILE="$2"; shift 2 ;;
    --repo)      REPO_FLAG="--repo $2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TITLE" || -z "$HEAD" ]]; then
  echo "Usage: $0 --title <title> --head <branch> [--base <base>] [--body <text>|--body-file <file>] [--repo <owner/repo>]" >&2
  exit 1
fi

if [[ -n "$BODY_FILE" ]]; then
  BODY_TEXT=$(cat "$BODY_FILE")
elif [[ -n "$BODY" ]]; then
  BODY_TEXT="$BODY"
else
  BODY_TEXT=""
fi

# shellcheck disable=SC2086
gh pr create \
  --title "$TITLE" \
  --head "$HEAD" \
  --base "$BASE" \
  --body "$BODY_TEXT" \
  $REPO_FLAG
