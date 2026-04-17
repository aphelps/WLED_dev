---
name: todo-handler
description: Read the git repo's todo.md (as well as todo.md of submodules), make plans for items on the todo list, and execute on those plans
---

## Overview

This skill manages a project's task lifecycle using `todo.md` as the source of truth.
Plans live as individual files in `todo_plans/` (sibling to `todo.md`).
All actions and state transitions are appended to `todo.log` (sibling to `todo.md`).

Task flow:
```
Task Ideas → Ready Tasks → Planning Task → Pending Tasks → Approved Tasks
                                                                ↓
                                          Blocked Tasks ← Active Tasks → Review Tasks → Completed Tasks
```

## Directory Layout

```
todo.md          # task list and state tracking
todo.log         # append-only timestamped action log
todo_plans/
  <task-slug>.md # one plan file per task
```

## todo.md Sections

| Section | Purpose |
|---------|---------|
| Task Ideas | Rough ideas; not yet scoped or ready to plan |
| Ready Tasks | Fully described; ready for planning |
| Planning Task | Currently being planned (at most one at a time) |
| Pending Tasks | Planned but awaiting user approval |
| Approved Tasks | Approved; ready to start execution |
| Active Tasks | Currently being executed by an agent |
| Blocked Tasks | Cannot proceed; waiting on external dependency |
| Review Tasks | PR open; awaiting review / merge |
| Completed Tasks | Done; commit or PR URL recorded |

## Canonical Task Entry Format

```markdown
* Task: <short title>
  * <goal description>
  * **Plan:** [todo_plans/<task-slug>.md](todo_plans/<task-slug>.md) _(added during planning)_
  * **Branch:** `<kebab-case-branch-name>` _(set when execution starts)_
  * **PR:** <PR URL or number> _(set when PR is opened)_
  * **Notes:** <context, decisions, links>
  * **Blocked by:** <reason> _(Blocked Tasks section only)_
```

Only include fields relevant to the current stage. The `task-slug` is a short kebab-case
identifier derived from the task title (e.g., `sparkle-fade-effect`).

## Plan File Format (`todo_plans/<task-slug>.md`)

```markdown
# <Task Title>

## Goal
<What this task should accomplish and why.>

## Subtasks
- [ ] Subtask 1
- [ ] Subtask 2 _(can run in parallel with subtask 3)_
- [ ] Subtask 3

## Notes
<Context, decisions, open questions, links.>
```

Update subtask checkboxes in the plan file as work progresses.

## todo.log Format

Each entry is one line:

```
YYYY-MM-DD HH:MM  [ACTION]       <task-slug>  <detail>
```

Actions: `MOVED`, `PLAN_CREATED`, `PLAN_UPDATED`, `BRANCH_CREATED`, `SUBTASK_DONE`,
`PR_OPENED`, `PR_UPDATED`, `BLOCKED`, `UNBLOCKED`, `MERGED`, `NOTE`.

Example:
```
2026-04-17 14:32  [MOVED]          sparkle-fade-effect  Ready Tasks → Planning Task
2026-04-17 14:45  [PLAN_CREATED]   sparkle-fade-effect  todo_plans/sparkle-fade-effect.md
2026-04-17 15:01  [MOVED]          sparkle-fade-effect  Planning Task → Pending Tasks
```

Always append to `todo.log`; never edit past entries.

## Startup

1. Read root `todo.md`; also check for `todo.md` in any git submodules.
2. **Active Tasks** present → ask user if any should be resumed. If yes → **Task Execution**.
3. **Planning Task** present → continue **Task Planning** for it.
4. **Pending Tasks** present → ask user if any should be approved. If yes → continue **Task Planning**.
5. **Blocked Tasks** present → check if any blocker is resolved; if so move to **Approved Tasks** and log `UNBLOCKED`.
6. Nothing actionable → report idle and exit.
7. Otherwise → **Task Planning**.

## Task Planning

1. Take the first item from **Ready Tasks**; move it to **Planning Task** in `todo.md`.
   Log `MOVED  <slug>  Ready Tasks → Planning Task`.
2. Read relevant source files to understand scope.
3. If the user is available, ask clarifying questions before finalizing.
4. Create `todo_plans/<task-slug>.md` with the goal, subtask checklist, and notes.
   Log `PLAN_CREATED`.
5. Add a `**Plan:**` link to the task entry in `todo.md`.
6. Present the plan to the user for approval.
7. **Idle parallelism:** while waiting for user feedback on the current Planning Task,
   begin researching and drafting a plan for the next **Ready Task**. Do not move it
   to Planning Task yet — hold the draft locally until the current task advances.
8. User approves → move task to **Approved Tasks**; log `MOVED … → Approved Tasks`.
   Ask if execution should start now. If yes → move to **Active Tasks** → **Task Execution**.
   User unavailable → move to **Pending Tasks**; log accordingly.

## Task Execution

1. If the task has no branch yet:
   - Choose a concise branch name (<40 chars, kebab-case).
   - Create the branch in the repo and any affected submodules.
   - Record branch name in the task entry; log `BRANCH_CREATED`.
2. Work through each subtask in the plan file, marking `[x]` as each completes.
   Log `SUBTASK_DONE` for each completed subtask.
3. Update `todo.md` and the plan file after each subtask so state is recoverable.
4. Follow the project's code style conventions.
5. Add unit / integration tests where applicable; all must pass before opening a PR.
6. Build the project and confirm it compiles / passes tests cleanly.
7. When all subtasks complete: commit, push, open PR.
   Move task to **Review Tasks**; record PR link in task entry; log `PR_OPENED`. Notify user.

## PR Handling

1. Check the PR for new comments or review requests since last check.
2. Answer reviewer questions with inline PR comments; log `PR_UPDATED`.
3. If CI fails: diagnose root cause and push a fix before requesting re-review.
4. If changes are requested: move task back to **Active Tasks** → **Task Execution**;
   log `MOVED … → Active Tasks`.
5. When PR is approved and CI is green:
   - If the base branch has new commits: rebase the PR branch onto base; resolve conflicts.
   - Squash to a single clean commit with a descriptive message.
   - Merge via rebase (no merge commit).
6. Move task to **Completed Tasks**; record commit URL or hash; log `MERGED`. Delete the branch.

## Blocked Tasks

If a task cannot proceed (hardware unavailable, upstream dependency, external decision):
1. Move to **Blocked Tasks**; record reason in `**Blocked by:**`; log `BLOCKED`.
2. At each **Startup**, review Blocked Tasks; if a blocker is resolved, move the task
   back to **Approved Tasks** and log `UNBLOCKED`.

## Task Ideas

When idle and **Task Ideas** are present:
- Research the idea and add findings / suggestions to the task entry.
- Do **not** move the task forward or begin planning without explicit user direction.

## When Idle (priority order)

1. **Review Tasks** present → **PR Handling**
2. **Active Tasks** with no running agent → continue first active task → **Task Execution**
3. **Approved Tasks** present → move first to **Active Tasks** → **Task Execution**
4. **Ready Tasks** present → **Task Planning**
5. **Task Ideas** present → research and annotate; do not advance
6. Nothing actionable → report idle and exit

## Submodule todo.md Files

After reading the root `todo.md`, check for `todo.md` files in any git submodules.
Apply the same workflow; prefix the task slug with the submodule name so plan files
and log entries are unambiguous.

## General Guidelines

- Follow the project's code style conventions for all code changes.
- Add unit / application tests for coding tasks; all must pass before opening a PR.
- Keep `todo.md`, plan files, and `todo.log` updated at every state transition.
- Prefer small, focused branches over large omnibus changes.
- Never open a PR for code that fails to compile or fails tests.
