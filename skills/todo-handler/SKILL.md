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
todo.md          # task list and state tracking       ← local only, not committed
todo.log         # append-only timestamped action log ← local only, not committed
todo_plans/
  <task-slug>.md # one plan file per task             ← local only, not committed
```

`todo.md`, `todo.log`, and `todo_plans/` are **not committed to the repository**.
Multiple tasks run in parallel on different branches; committing these shared files
causes merge conflicts. All three must be listed in `.gitignore`.

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
  * **PR:** <base repo PR URL> _(set when PR is opened)_
  * **Submodule PRs:** `<submodule-path>`: <PR URL> _(one line per affected submodule; omit if none)_
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
   - Determine the remote default base: use `origin/main` if it exists, otherwise `origin/master`.
   - Run `skills/todo-handler/scripts/create-task-branch.sh <branch> [submodule ...]`
     which fetches `origin/main` (falling back to `origin/master`) and creates the branch
     in the parent repo and each listed submodule from the remote ref.
   - Record the branch name in the task entry; log `BRANCH_CREATED`.
2. Work through each subtask in the plan file, marking `[x]` as each completes.
   Log `SUBTASK_DONE` for each completed subtask.
3. Update `todo.md` and the plan file after each subtask so state is recoverable.
4. Follow the project's code style conventions.
5. Add unit / integration tests where applicable; all must pass before opening a PR.
6. Build the project and confirm it compiles / passes tests cleanly.
7. When all subtasks complete:
   - For each submodule with new commits on the task branch:
     a. Push the submodule branch: `git push origin <branch>` from inside the submodule.
     b. Open a submodule PR targeting the submodule's default branch. In the description,
        include: "Part of <base-repo-PR-URL> in the parent repo."
     c. Record the submodule PR URL in the task entry under `**Submodule PRs:**`; log `PR_OPENED`.
   - Push the parent repo branch and open the base repo PR. In the description, include a
     "Submodule PRs" section linking to all submodule PRs.
   - Do **not** squash at this point — the branch may have multiple commits and that is fine.
     Squashing happens only immediately before the final merge (see PR Handling step 5).
   - Move task to **Review Tasks**; record all PR links in the task entry; log `PR_OPENED`. Notify user.

## PR Handling

1. Check **every open PR for the task** — the base repo PR and each submodule PR listed
   under `**Submodule PRs:**`. For each PR, fetch **both** sources of feedback in the same pass:
   - Review-level comments: `gh api repos/<owner>/<repo>/pulls/<n>/reviews`
   - Inline (diff) comments: `gh api repos/<owner>/<repo>/pulls/<n>/comments`
   These are separate API endpoints; inline comments do **not** appear in the reviews list.
   Never handle only the base repo PR in isolation.
2. For each unresolved comment (review-level or inline):
   - If clear and actionable: address it, reply to the comment thread, and resolve the thread.
   - If clarification needed: reply asking the specific question; if a user is present, ask here too.
   After addressing all comments, log `PR_UPDATED`.
3. If CI fails: diagnose root cause and push a fix before requesting re-review.
4. If changes are requested: move task back to **Active Tasks** → **Task Execution**;
   log `MOVED … → Active Tasks`. Address the feedback and push each fix as a **new
   commit** — do not squash or rebase the branch. Reviewers need to see what changed
   since their last review; squashing erases that signal.
5. When all PRs are approved and CI is green:
   - **Submodule PRs first:** for each submodule PR:
     a. If the submodule's base branch has new commits: rebase and resolve conflicts.
     b. Squash to a single commit; merge via rebase.
   - After all submodule PRs are merged: update the submodule pointer(s) in the parent branch and push.
   - If the parent repo's base branch has new commits: rebase the parent branch; resolve conflicts.
   - **Squash immediately before merge** (and only now):
     `skills/todo-handler/scripts/squash-branch.sh origin/<base> "<message>"`.
   - Merge via rebase (no merge commit): `gh pr merge --rebase --delete-branch`.
6. Move task to **Completed Tasks**; record all commit URLs/hashes; log `MERGED`. Delete all branches (parent + submodules).

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
- Keep `todo.md`, plan files, and `todo.log` updated at every state transition. Never commit any of them.
- Prefer small, focused branches over large omnibus changes.
- Never open a PR for code that fails to compile or fails tests.
- **Never use `git reset --hard`** (or `git checkout -- .` / `git restore .`). These destroy untracked
  and gitignored files such as `todo.md`, `todo.log`, and `todo_plans/` with no recovery path.
  To squash branch commits safely, use `git reset --soft <ref>` which preserves the working tree.
  To start a branch from a known remote ref, create a new branch with `git checkout -b <name> <ref>`
  rather than resetting an existing branch in place.
- **Never use interactive commands** that open an editor or require keyboard input:
  forbidden: `git rebase -i`, `git add -p`, `git commit` (without `-m`), `git merge` (without
  `--no-edit`), `gh pr create` (without `--title` and `--body`). Use the helper scripts in
  `skills/todo-handler/scripts/` for common multi-step operations.
- **Human input is only required at three points:** (1) approving a plan, (2) confirming
  execution should start, (3) reviewing a PR. All other steps run to completion autonomously.

## Non-Interactive Command Reference

Use these recipes (or the corresponding scripts) for all common operations.

### Squash branch commits into one
```bash
# From the branch to squash; <base> is typically origin/main
git reset --soft <base>
git commit -m "$(cat <<'EOF'
<commit message>
EOF
)"
# Or use the script:
skills/todo-handler/scripts/squash-branch.sh "<base>" "<commit message>"
```

### Create a task branch (parent repo + submodules)
```bash
git fetch origin main
git checkout -b <branch> origin/main
# For each submodule that will be modified:
cd <submodule-path> && git fetch origin main && git checkout -b <branch> origin/main && cd -
# Or use the script:
skills/todo-handler/scripts/create-task-branch.sh <branch> [submodule-path ...]
```

### Update submodule pointer
```bash
git add <submodule-path>
git commit -m "Update <submodule> submodule pointer"
```

### Open a PR (non-interactive)
```bash
gh pr create --title "<title>" --base main --head <branch> --body "$(cat <<'EOF'
<body>
EOF
)"
# Or use the script:
skills/todo-handler/scripts/open-pr.sh --title "<title>" --head <branch> --body-file <file>
```
