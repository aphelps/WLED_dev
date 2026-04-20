#!/usr/bin/env bash
# check-for-work.sh [todo-md-path]
#
# Lightweight pre-check for the todo-handler skill. Reads todo.md and, for
# any Review Tasks, fetches the latest GitHub PR activity. Exits 0 with a
# one-line summary if work is found; exits 1 with "IDLE" if nothing to act on.
#
# Called by the skill at wake-up to decide whether to proceed with a full
# Claude pass or simply reschedule.
#
# Output (stdout):
#   WORK: <reason>   — exit 0 — Claude should proceed
#   IDLE             — exit 1 — nothing to do, reschedule
#
# Requires: gh (GitHub CLI), jq

set -euo pipefail

TODO="${1:-todo.md}"

if [[ ! -f "$TODO" ]]; then
  echo "WORK: todo.md not found — needs investigation"
  exit 0
fi

# ── Parse todo.md for actionable sections ────────────────────────────────────

in_section=""
has_active=0
has_approved=0
has_ready=0
has_planning=0
has_blocked=0
review_prs=()   # "owner/repo#num" entries

while IFS= read -r line; do
  case "$line" in
    "## Active Tasks")    in_section="active" ;;
    "## Approved Tasks")  in_section="approved" ;;
    "## Ready tasks"|"## Ready Tasks") in_section="ready" ;;
    "## Planning Task")   in_section="planning" ;;
    "## Blocked Tasks")   in_section="blocked" ;;
    "## Review Tasks")    in_section="review" ;;
    "## "*)               in_section="" ;;
  esac

  [[ -z "$in_section" ]] && continue

  # Count non-empty task lines
  if [[ "$line" =~ ^\*\ Task: ]]; then
    case "$in_section" in
      active)   has_active=1 ;;
      approved) has_approved=1 ;;
      ready)    has_ready=1 ;;
      planning) has_planning=1 ;;
      blocked)  has_blocked=1 ;;
    esac
  fi

  # Collect PR URLs from Review Tasks (base repo and submodule PRs)
  if [[ "$in_section" == "review" ]] && [[ "$line" =~ https://github\.com/([^/]+/[^/]+)/pull/([0-9]+) ]]; then
    review_prs+=("${BASH_REMATCH[1]}#${BASH_REMATCH[2]}")
  fi
done < "$TODO"

# ── Immediate work: anything other than Review Tasks ─────────────────────────

[[ $has_active   -eq 1 ]] && { echo "WORK: Active Tasks present"; exit 0; }
[[ $has_approved -eq 1 ]] && { echo "WORK: Approved Tasks ready to execute"; exit 0; }
[[ $has_ready    -eq 1 ]] && { echo "WORK: Ready Tasks need planning"; exit 0; }
[[ $has_planning -eq 1 ]] && { echo "WORK: Planning Task in progress"; exit 0; }
[[ $has_blocked  -eq 1 ]] && { echo "WORK: Blocked Tasks may be unblocked"; exit 0; }

# ── Review Tasks: check for new PR activity ──────────────────────────────────

if [[ ${#review_prs[@]} -eq 0 ]]; then
  echo "IDLE"
  exit 1
fi

for pr_ref in "${review_prs[@]}"; do
  repo="${pr_ref%%#*}"
  num="${pr_ref##*#}"

  # Fetch latest review decision and updated_at timestamp
  result=$(gh api "repos/$repo/pulls/$num" \
    --jq '{state, draft, reviewDecision: (.auto_merge // "none"),
           updated: .updated_at}' 2>/dev/null) || continue

  state=$(echo "$result" | jq -r '.state')
  [[ "$state" == "closed" ]] && { echo "WORK: PR $repo#$num is closed/merged"; exit 0; }

  # Check for new review activity in the last 2 hours
  updated=$(gh api "repos/$repo/pulls/$num" --jq '.updated_at' 2>/dev/null) || continue
  updated_epoch=$(date -d "$updated" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%SZ" "$updated" +%s 2>/dev/null) || continue
  now_epoch=$(date +%s)
  age=$(( now_epoch - updated_epoch ))

  if [[ $age -lt 7200 ]]; then   # updated within 2 hours
    echo "WORK: PR $repo#$num updated recently ($((age/60)) min ago)"
    exit 0
  fi
done

echo "IDLE"
exit 1
