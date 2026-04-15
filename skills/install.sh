#!/usr/bin/env bash
# Creates symlinks from each skill into tool-specific command directories.
# Run from anywhere; paths are resolved relative to this script's location.
#
# Claude Code: .claude/skills/<name>/SKILL.md  (project-level)
# Codex CLI:   .agents/skills/<name>.md        (project-level)

set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SKILLS_DIR")"

CLAUDE_DIR="$ROOT/.claude/skills"
CODEX_DIR="$ROOT/.agents/skills"

linked=0; skipped=0

for skill_dir in "$SKILLS_DIR"/*/; do
  name="$(basename "$skill_dir")"
  src="$skill_dir/SKILL.md"
  [[ -f "$src" ]] || continue

  # Claude Code: .claude/skills/<name> -> skills/<name>
  claude_target="$CLAUDE_DIR/$name"
  mkdir -p "$CLAUDE_DIR"
  if [[ ! -L "$claude_target" ]]; then
    rel="$(python3 -c "import os; print(os.path.relpath('$skill_dir', '$CLAUDE_DIR'))")"
    ln -s "$rel" "$claude_target"
    echo "  claude: $name"
    ((linked++))
  else
    ((skipped++))
  fi

  # Codex CLI: .agents/skills/<name>.md
  codex_target="$CODEX_DIR/$name.md"
  mkdir -p "$(dirname "$codex_target")"
  if [[ ! -L "$codex_target" ]]; then
    rel="$(python3 -c "import os; print(os.path.relpath('$src', '$(dirname "$codex_target")'))")"
    ln -s "$rel" "$codex_target"
    echo "  codex:  $name"
    ((linked++))
  else
    ((skipped++))
  fi
done

echo "Done: $linked linked, $skipped already present."
