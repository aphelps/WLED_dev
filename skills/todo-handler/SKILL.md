---
name: todo-handler
description: Read the git repo's todo.md (as well as todo.md of submodules, make plans for items on the todo list, and execute on those plans
---

## Startup
* Check "Active Tasks".  Ask user if an active task should be resumed, if so 
  goto **Task Execution**
* If "Planning Task" is present then continue **Task Planning** for that task
* If "Pending Tasks" then ask user if they want to complete planning any of 
  those tasks, if so then continue **Task Planning** for that task
* goto **Task Planning**

## Task Planning
* Parse repo's todo.md
* Take first item from "Ready Tasks", move item to "Planning Task"
* Begin planning the task.  If user is available, ask questions to elaborate on  
  the task as needed.  Record relevant notes within the task.
* Break task into subtasks as needed or where sub-agents can work in parallel
* If user not available or task is detailed enough, complete plan for the task.
* If user available, iterate on plan until user approves, move to 
  "Approved Tasks".  Ask user if task should be started, if so move task to 
  "Active Tasks" and goto **Task Execution**, otherwise goto **Task Planning**
* If user not available move task to "Pending Tasks" and goto **Task Planning**

## Task Execution
* Keep notes in todo.md on each stage of an active task.  Record session 
identifier in case restarting the task becomes necessary
* If task is not started, create branches in the repo and all necessary 
submodules, choose a short (<40 chars) branch name
* Execute the task's plan and all tests.
* When complete, commit changes, push to remote and open PR if not already 
  opened.  Move to "Review Tasks" and record PR link.  Notify user of PR 
  readiness.

## PR Handling
* Check PR for changes since last check
* Answer questions asked in comments
* If changes requested move task to "Active Tasks" and return to
  **Task Execution**.
* When PR approved, flatten to a single commit.  
* If the base branch has changed, pull in changes. Goto **Task Execution**
* Merge via rebase.  Move task to "Completed Tasks" and record Github link of
  the commit.  

## When idle
* If tasks in "Review Tasks", goto **PR Handling**.
* If tasks in "Active Tasks" that have no agent working on them, continue first
  active task
* If "Ready Tasks" is not empty, goto **Task Planning**
* If tasks in "Approved Tasks" move one to "Active Tasks" and goto 
  **Task Execution**
* If tasks in "Task Ideas" do research and make suggestions in the task, but do
  not move the task

## General plan instructions
* For coding tasks, add unit and application tests when possible
* Code style should follow _amp-coding-style_ skill
* Build code and execute tests, all must pass before creating PR