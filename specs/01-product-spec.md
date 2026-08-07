<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->
---
status: proposed
---

# 01 — Product & Vision Spec

## Problem

A todo list is a list of *intentions*, not of *work*. Each line ("migrate the
export script", "write the Q3 summary", "clean up the photos folder") hides an
unknown number of concrete steps, and the cost of working out those steps is
most of the reason items sit untouched for weeks.

Handing a todo line straight to an AI agent does not fix this. A single agent
given "migrate the export script" will produce *something*, but nothing checks
that what it produced actually works, and nothing stops it declaring victory on
step one while steps two through five were never noticed. The failure mode is
not refusal — it is **confident, unverified, partial completion**.

## Vision

A system that takes a plain todo list and, for each item, does the boring
discipline a careful person would do:

1. **Break it down** into concrete, individually checkable subtasks.
2. **Do one subtask at a time**, in order.
3. **Prove each one worked** — with a real, executable check — before the next
   subtask starts.
4. **Stop and say so** when a subtask cannot be proven, rather than continuing
   on a broken foundation.

The unit of trust is the **verified subtask**, not the agent's own claim of
success. An item is done when every one of its subtasks passed its own check.

## Primary user

A single technical owner running the tool on their own machine against their own
todo list — comfortable editing a config file, reading a terminal, and reviewing
a git diff. Single-user by design in v1: this is a personal work-doer, not a
team task-management product.

## Goals

1. **Ingest** a plain, human-writable todo list (Markdown checkboxes) as the
   source of work — no proprietary format, no database to seed.
2. **Decompose** each todo item into an ordered list of subtasks, each with an
   explicit, machine-checkable **acceptance check**.
3. **Execute** subtasks strictly one at a time, in dependency order.
4. **Verify** each subtask against its own check immediately after execution,
   using evidence the system gathers itself (a command's exit status, a file's
   contents, a test run) — never the executing agent's self-report alone.
5. **Gate** progress on that verification: a failed check blocks the next
   subtask, triggers a bounded number of repair attempts, then stops the item.
6. **Journal** every decomposition, execution, and verification to durable state
   so a run is resumable, auditable, and never silently redoes finished work.
7. **Be spec-driven about itself** — the system's own behaviour is described in
   `specs/`, and its own code is built by a spec loop reconciling code against
   those specs (see `04-technical-plan.md`).

## Non-goals (v1)

- **Not** a shared/team task tracker, and **not** a sync client for Jira, Asana,
  Todoist, or similar. The todo list is a local file.
- **Not** a scheduler or daemon. Runs are started by the human (or by their own
  cron), not by a resident service.
- **Not** an unattended actor on the outside world: no sending mail, no pushing
  branches, no opening PRs, no purchases. Side effects reach outward only
  through steps the human explicitly enabled.
- **Not** a general chat assistant. There is no conversational mode; the todo
  file is the interface.
- **Not** a replacement for human judgement on *what* to do — it decides how to
  do an item, not whether the item is worth doing.

## Success criteria

Qualitative goals the product is judged by. The *testable* acceptance criteria
live per-stage in `02-functional-spec.md` — this list says what "good" feels
like; 02 says what a test can check.

- I can write an ordinary Markdown todo list, run one command, and come back to
  find items either **done and demonstrably working**, or **stopped with a clear
  reason** — never "done" but broken.
- Every completed subtask has **evidence** attached: the command that was run
  and its output, or the file that was written and its check.
- A run interrupted halfway (Ctrl+C, crash, power loss) resumes without redoing
  verified subtasks and without losing the journal.
- When an item fails, the failure names the **specific subtask and check** that
  did not pass, in language I can act on.
- A dry run shows me the full decomposition — subtasks and their checks — before
  anything is executed, so I can veto a bad plan cheaply.
- Adding a new kind of check (a new verifier) does not require touching the
  execution engine.

## Guiding principles

- **Verification is the product.** Anything can generate work; the value here is
  refusing to advance on unproven work. If a subtask has no meaningful check, it
  is not ready to run.
- **One subtask at a time.** Sequential and boring beats parallel and
  untraceable. Concurrency is a later, opt-in optimisation, never the default.
- **Evidence over assertion.** The agent saying "done" is an input, not a
  verdict. The verdict comes from a re-run check the system performs itself.
- **Fail loud, fail early, fail small.** Stop the item at the first unrepaired
  failure. Never carry a broken subtask forward hoping later work fixes it.
- **Resumable by construction.** Every state transition is journalled before the
  next one starts, so the durable record is always ahead of the side effects.
- **Least authority.** Default to a sandboxed, no-network, no-push execution
  context; capability is granted per-item, explicitly, in config.
- **Config over code.** Adjusting decomposition depth, retry budgets, allowed
  tools, or the verification policy is a config change, not a code change.
