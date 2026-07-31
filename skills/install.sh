#!/usr/bin/env bash
# Link every skill in this directory into the locations the tools read:
#
#   Claude Code: .claude/skills/<name>/SKILL.md   (symlink to the skill directory)
#   Codex CLI:   .agents/skills/<name>.md         (symlink to the SKILL.md itself)
#
# Safe to run any number of times. Links are rewritten rather than skipped, so one that is broken
# or points somewhere stale gets repaired instead of being reported as "already present" — that is
# exactly what renaming a skill leaves behind, and skipping it is how a skill quietly stops
# loading while the script still says everything is fine.
#
# Run from anywhere; paths resolve relative to this script.
#
# Usage: install.sh [--dry-run]

set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

SKILLS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SKILLS_DIR")"
CLAUDE_DIR="$ROOT/.claude/skills"
CODEX_DIR="$ROOT/.agents/skills"

linked=0
pruned=0
problems=0

relpath() { python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$1" "$2"; }

# link <source> <target> — always (re)create, so the outcome never depends on prior state.
link() {
  local src="$1" target="$2" rel
  rel="$(relpath "$src" "$(dirname "$target")")"

  # Never clobber something that is not a symlink: a real file or directory there is someone's
  # work, not ours to delete.
  if [[ -e "$target" && ! -L "$target" ]]; then
    echo "  SKIP  $target — exists and is not a symlink" >&2
    problems=$((problems + 1))
    return 0
  fi

  if [[ "$DRY_RUN" == 1 ]]; then
    echo "  would link $(basename "$target") -> $rel"
    return 0
  fi

  mkdir -p "$(dirname "$target")"
  # -n matters: without it, linking over an existing symlink-to-a-directory creates the new link
  # *inside* that directory rather than replacing it.
  ln -sfn "$rel" "$target"
  linked=$((linked + 1))
  return 0
}

# --- link every skill --------------------------------------------------------------------------
shopt -s nullglob
for skill_dir in "$SKILLS_DIR"/*/; do
  name="$(basename "$skill_dir")"
  src="$skill_dir/SKILL.md"

  if [[ ! -f "$src" ]]; then
    echo "  SKIP  $name — no SKILL.md" >&2
    problems=$((problems + 1))
    continue
  fi

  link "$skill_dir" "$CLAUDE_DIR/$name"
  link "$src"       "$CODEX_DIR/$name.md"
done

# --- prune links whose skill no longer exists ---------------------------------------------------
# Only touches symlinks pointing back into skills/, so anything else living in these directories is
# left alone. A dangling one is the residue of a renamed or deleted skill.
prune_dir() {
  local dir="$1" link_path target
  [[ -d "$dir" ]] || return 0
  for link_path in "$dir"/*; do
    [[ -L "$link_path" ]] || continue
    target="$(readlink "$link_path")"
    case "$target" in
      *skills/*) ;;
      *) continue ;;
    esac
    [[ -e "$link_path" ]] && continue          # -e follows the link; false means dangling
    if [[ "$DRY_RUN" == 1 ]]; then
      echo "  would prune $(basename "$link_path") (dangling -> $target)"
    else
      rm -f "$link_path"
      echo "  pruned: $(basename "$link_path")"
      pruned=$((pruned + 1))
    fi
  done
  return 0
}

prune_dir "$CLAUDE_DIR"
prune_dir "$CODEX_DIR"

# --- link skill commands onto PATH --------------------------------------------------------------
# Any executable in skills/<name>/bin/ becomes a runnable command. Linked into $SKILLS_BIN, or
# ~/.local/bin when that already exists — deliberately not created, so this never invents a PATH
# directory on a machine that does not use that convention. Say where it went; a command that
# silently lands somewhere unexpected is worse than one that is not installed.
BIN_DIR="${SKILLS_BIN:-$HOME/.local/bin}"
if [[ -d "$BIN_DIR" ]]; then
  for cmd in "$SKILLS_DIR"/*/bin/*; do
    [[ -f "$cmd" && -x "$cmd" ]] || continue
    link "$cmd" "$BIN_DIR/$(basename "$cmd")"
  done
  prune_dir "$BIN_DIR"
  case ":${PATH}:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  note: $BIN_DIR is not on your PATH — commands linked there will not be found" >&2 ;;
  esac
else
  for cmd in "$SKILLS_DIR"/*/bin/*; do
    [[ -f "$cmd" && -x "$cmd" ]] || continue
    echo "  note: $BIN_DIR does not exist — skipping command '$(basename "$cmd")'" >&2
    echo "        create it (or set SKILLS_BIN=<dir>) and re-run to install it" >&2
  done
fi

# --- report ------------------------------------------------------------------------------------
if [[ "$DRY_RUN" == 1 ]]; then
  echo "Dry run — nothing changed."
else
  echo "Done: $linked link(s) written, $pruned pruned."
fi

if [[ "$problems" -gt 0 ]]; then
  echo "$problems item(s) need attention (see SKIP above)." >&2
  exit 1
fi
