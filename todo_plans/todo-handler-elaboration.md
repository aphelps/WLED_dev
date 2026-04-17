# Elaborate on the todo-handler skill

## Goal
Improve `skill.md` so the todo-handler skill handles more edge cases, is clearer
about state transitions, and works well in both attended and unattended (autonomous
loop) modes. Produce a reusable blank `todo-template.md`. Add "Blocked Tasks"
infrastructure to the live `todo.md` and introduce `todo.log` for action tracking.

## Subtasks
- [x] Rewrite `skill.md` with: canonical task format, full section table, clearer
      startup/idle flow, idle parallelism, submodule support, session tracking,
      Blocked Tasks handling, PR CI-failure handling, todo.md format guidance,
      and todo.log spec
- [x] Create `todo-template.md` in the skill directory showing all 9 sections and
      canonical task entry syntax
- [x] Add "Blocked Tasks" section to `todo.md`; move this task to Pending Tasks
      with a plan link; create `todo.log`

## Notes
- Skill lives at `.claude/skills/todo-handler/skill.md`
- Plans are stored in `todo_plans/` (sibling to `todo.md`), not embedded in `todo.md`
- `todo.log` is append-only; never edit past entries
- Skill must remain project-agnostic (no build-system specifics)
