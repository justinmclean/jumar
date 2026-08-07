<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->
---
status: proposed
---

# 02 — Functional Spec

The pipeline, stage by stage. Each stage states **where it lives**, its
**behaviour & contract**, its **acceptance criteria** (what a test checks), and
its **known gaps**. Shapes referenced here are defined in `03-data-model.md`.

The pipeline is:

```
todo file  →  1 ingest  →  2 select  →  3 decompose  →  4 gate (dry-run/approve)
           →  5 execute one subtask  →  6 verify it  →  7 repair (bounded)
           →  (loop 5–7 for the next subtask)  →  8 complete item  →  9 report
```

Stages 5–7 are the **inner loop**, run once per subtask. Stages 2–8 are the
**outer loop**, run once per todo item.

---

## Stage 1 — Ingest

**Where it lives:** `src/getstuffdone/ingest.py`

**Behaviour & contract**

Reads the todo file (default `todo.md`, overridable) and parses GitHub-flavoured
Markdown task list items into `TodoItem` records.

- `- [ ] text` → pending item. `- [x] text` → already-done item, skipped.
- Nesting: an indented task list under an item is read as **author-supplied
  subtasks** and short-circuits stage 3 for that item (the human's breakdown
  wins over the model's).
- Inline metadata in trailing `@key=value` tokens is parsed into the item's
  `meta` map: `@id=`, `@priority=`, `@depends=`, `@check=`, `@capability=`,
  `@max-subtasks=`. Unknown keys are preserved and ignored, never an error.
- A stable `item_id` is assigned: `@id=` if present, else a slug of the text
  plus a short hash of the text, so re-ordering the file does not orphan state.
- Non-task-list lines (headings, prose, blank lines) are retained as `context`
  attached to the following item, and are never treated as work.

**Acceptance criteria**

- AC1.1 A file of `N` unchecked items yields exactly `N` pending `TodoItem`s.
- AC1.2 `- [x]` items are parsed but marked `done` and never selected.
- AC1.3 An indented task list under an item populates `item.authored_subtasks`
  in file order, and the item is flagged `decomposition=authored`.
- AC1.4 `@priority=1 @depends=abc` parses into `meta` with the raw values kept.
- AC1.5 `item_id` is stable across a re-run when the item's text is unchanged,
  and changes when the text changes.
- AC1.6 A malformed line does not abort ingest; it is recorded as a parse
  warning on the run and the remaining items still parse.
- AC1.7 A missing todo file is a clean, actionable error, not a traceback.

**Known gaps**

- Only Markdown task lists in v1. Other formats are out of scope.

---

## Stage 2 — Select

**Where it lives:** `src/getstuffdone/select.py`

**Behaviour & contract**

Chooses the single next item to work. Ordering: explicit `@priority` ascending,
then file order. An item whose `@depends=` names an item that is not `done` is
skipped (not failed) and reported as blocked. An item already `done` or `failed`
in the journal for this run is never re-selected.

Cycles in `@depends` are detected up front and reported as a configuration
error before any work starts.

**Acceptance criteria**

- AC2.1 With no priorities, selection returns items in file order.
- AC2.2 `@priority=1` is selected before an unprioritised earlier item.
- AC2.3 An item depending on an unfinished item is skipped and listed as
  blocked, and the next eligible item is selected instead.
- AC2.4 A dependency cycle raises a configuration error naming the cycle, and
  no item is executed.
- AC2.5 Items marked `done` in the journal are not re-selected on resume.

---

## Stage 3 — Decompose

**Where it lives:** `src/getstuffdone/decompose.py`

**Behaviour & contract**

Turns one `TodoItem` into an ordered `Plan` of `Subtask`s by calling the agent
harness (`04-technical-plan.md` §Harness) with the decomposition prompt.

Every returned subtask **must** carry:

- `description` — what to do, imperative, one step.
- `check` — a `Check` record (see `03-data-model.md`): the executable proof that
  this subtask succeeded. `kind` is one of `command`, `file`, `absence`,
  `judge`, `manual`.
- `capabilities` — the tool/network/filesystem authority the subtask needs,
  drawn from the item's granted capability set.

Hard rules enforced by the code, not by the prompt:

- A subtask with no `check`, or with an empty/placeholder check, is **rejected**;
  the decomposition is retried once, then the item fails with
  `unverifiable_plan`. "Trust me" is not a check.
- A `judge` check (an independent agent verdict) is only accepted when the
  subtask's nature makes an executable check impossible — and the plan must say
  why in `check.rationale`.
- The plan is capped at `max_subtasks` (config, default 12; `@max-subtasks=`
  overrides). A longer plan is rejected and the item is flagged for the human to
  split — a 40-step item is a mis-sized todo, not a long run.
- Subtasks are ordered; `depends_on` between subtasks is allowed but must form a
  DAG within the item.
- If the item has `authored_subtasks` (AC1.3), those become the plan verbatim,
  and the agent is asked only to supply a `check` for any that lack one.

**Acceptance criteria**

- AC3.1 A decomposition response missing `check` on any subtask is rejected and
  retried exactly once; a second failure fails the item as `unverifiable_plan`.
- AC3.2 A plan longer than `max_subtasks` is rejected with `plan_too_long` and
  no subtask is executed.
- AC3.3 Authored subtasks are used verbatim, in file order, with the model only
  filling in missing checks.
- AC3.4 A `judge` check without `rationale` is rejected.
- AC3.5 A cyclic `depends_on` within a plan is rejected as `invalid_plan`.
- AC3.6 The full plan (subtasks + checks) is journalled before any execution.

**Known gaps**

- No cross-item decomposition: each item is planned in isolation.

---

## Stage 4 — Gate

**Where it lives:** `src/getstuffdone/gate.py`

**Behaviour & contract**

Between plan and execution the run consults its **mode**:

- `--dry-run` — print the plan (subtasks, checks, capabilities), journal it,
  execute nothing. Exit 0.
- `--approve` — print the plan and wait for the human's `y/n` on stdin. `n`
  marks the item `skipped_by_human` and moves on.
- default (`--auto`) — proceed straight to execution.

Independently of mode, a plan requesting a capability the item was not granted
(`@capability=` / config) is **refused** before execution, naming the subtask and
the capability.

**Acceptance criteria**

- AC4.1 `--dry-run` produces the full plan output, writes the journal entry, and
  makes zero execution calls.
- AC4.2 `--approve` with `n` on stdin records `skipped_by_human` and does not
  execute.
- AC4.3 A subtask requesting an ungranted capability aborts the item with
  `capability_denied` and names the subtask and capability.

---

## Stage 5 — Execute one subtask

**Where it lives:** `src/getstuffdone/execute.py`

**Behaviour & contract**

Runs exactly **one** subtask, in the item's working directory, via the agent
harness, with:

- the subtask description, the parent item text, and the item `context`;
- the accumulated **evidence** from previously verified subtasks of this item
  (so step 4 knows what step 3 actually produced);
- the subtask's granted capabilities and no others;
- a wall-clock timeout (config, default 900 s) after which the subtask is
  `timed_out`.

The executor never marks a subtask successful. It records an `Attempt`
(`started_at`, `finished_at`, agent transcript reference, exit status, files
touched) and hands straight to stage 6. The agent's own "I'm done" is stored as
a *claim* on the attempt, never as the outcome.

**Acceptance criteria**

- AC5.1 Exactly one subtask is dispatched per execute call; subtask `n+1` is
  never dispatched before subtask `n` has a verification verdict.
- AC5.2 An attempt exceeding the timeout is terminated and recorded as
  `timed_out`, and the item does not advance.
- AC5.3 The evidence from verified prior subtasks is present in the prompt for
  the current subtask.
- AC5.4 A subtask's attempt is journalled before its verification runs.
- AC5.5 An agent claiming success never by itself produces a `passed` subtask.

---

## Stage 6 — Verify

**Where it lives:** `src/getstuffdone/verify/` (one module per check kind, plus
a registry)

**Behaviour & contract**

Runs the subtask's `check` and produces a `Verdict` (`passed` / `failed` /
`inconclusive`) with the evidence that produced it. Verification runs in a
**fresh context** — it never sees the executing agent's reasoning, only the
world it left behind.

Check kinds:

| kind | passes when | evidence recorded |
|---|---|---|
| `command` | the command exits 0 (configurable expected status) | argv, exit code, captured stdout/stderr (truncated) |
| `file` | the named path exists and matches the required pattern/predicate | path, size, hash, matched excerpt |
| `absence` | the named path/pattern is gone | path, resolved glob |
| `judge` | an independent agent, given only the artefacts and the acceptance statement, returns `pass` | verdict, one-line reason, artefact list |
| `manual` | the human confirms at the prompt | who, when, response |

Rules:

- A `judge` verifier is prompted **adversarially** — its default answer is fail,
  and it must state the specific evidence that changed its mind.
- `inconclusive` (check could not run — missing binary, unreachable path) is
  **not** a pass. It routes to repair like a failure, and if unresolved the item
  ends as `inconclusive`, distinct from `failed`.
- A `manual` check in a non-interactive run is `inconclusive`, never auto-passed.
- Verifier output is truncated to a configured byte cap in the journal, with the
  full output written alongside as an artefact file.

**Acceptance criteria**

- AC6.1 A `command` check exiting non-zero yields `failed` with the exit code
  and captured output in the evidence.
- AC6.2 A `file` check for a path the subtask did not create yields `failed`.
- AC6.3 A missing verifier binary yields `inconclusive`, not `failed` and not
  `passed`.
- AC6.4 The `judge` verifier receives no part of the executing agent's
  transcript — asserted by a test that inspects the assembled verifier prompt.
- AC6.5 A `manual` check under `--non-interactive` yields `inconclusive`.
- AC6.6 Every verdict is journalled with its evidence before the next subtask
  starts.
- AC6.7 Adding a new check kind requires only registering a verifier in the
  registry — asserted by a test that registers a dummy kind end to end.

---

## Stage 7 — Repair (bounded)

**Where it lives:** `src/getstuffdone/repair.py`

**Behaviour & contract**

On `failed` or `inconclusive`, the subtask is retried up to `max_repairs`
(config, default 2) times. Each repair attempt receives the failing verdict and
its evidence, and is told explicitly what did not pass. After each repair the
**same check** is re-run — a repair may never rewrite its own acceptance check.

If the budget is exhausted, the item stops with `failed_at_subtask=<n>`.
Remaining subtasks are not attempted, the todo file is left untouched, and the
run moves to the next eligible item (or halts entirely under `--halt-on-fail`).

**Acceptance criteria**

- AC7.1 A failing subtask is retried exactly `max_repairs` times, no more.
- AC7.2 The repair prompt contains the failing check and its evidence.
- AC7.3 A repair attempt that modifies the subtask's `check` is rejected and the
  original check is re-run.
- AC7.4 On budget exhaustion the item is `failed`, later subtasks are not run,
  and the next eligible item is selected (default) or the run halts
  (`--halt-on-fail`).

---

## Stage 8 — Complete item

**Where it lives:** `src/getstuffdone/complete.py`

**Behaviour & contract**

An item is `done` only when **every** subtask in its plan has a `passed`
verdict. On completion:

- the todo file's checkbox for that item is flipped `- [ ]` → `- [x]`, in place,
  preserving the rest of the file byte for byte;
- the journal records the completion with the per-subtask evidence;
- if the item ran in a git repository and `commit_on_complete` is enabled, the
  changes are committed on a per-item branch (never the base branch), with the
  item text as the subject. **The system never pushes and never opens a PR.**

**Acceptance criteria**

- AC8.1 An item with any non-passed subtask is never marked `- [x]`.
- AC8.2 Flipping the checkbox changes only that line; a byte-diff of the rest of
  the file is empty.
- AC8.3 With `commit_on_complete`, the commit lands on a per-item branch and the
  base branch's HEAD is unchanged.
- AC8.4 No push and no PR command is ever invoked — asserted by a test that
  fails if `git push` or `gh` appears in the dispatched argv.

---

## Stage 9 — Report

**Where it lives:** `src/getstuffdone/report.py`

**Behaviour & contract**

Writes a per-run report to `runs/<run-id>/report.md` and prints a summary: items
done / failed / blocked / skipped, and for each failure the subtask and the
check that did not pass. Exit status is 0 if every selected item completed, 1 if
any item failed or was inconclusive.

**Acceptance criteria**

- AC9.1 The report names, for each failed item, the failing subtask index, its
  description, and the check's evidence summary.
- AC9.2 Exit status is 0 on a fully clean run and 1 when any item failed.
- AC9.3 The report is written even when the run is interrupted mid-item, from
  the journal (partial report, clearly marked).

---

## Cross-cutting: state & resume

**Where it lives:** `src/getstuffdone/journal.py`

Every stage transition is appended to `runs/<run-id>/journal.jsonl` **before**
the next stage begins. `gsd resume <run-id>` replays the journal, restores item
and subtask status, and continues from the first subtask without a verdict.

- AC-S1 A run killed mid-execution and resumed does not re-execute any subtask
  that already has a `passed` verdict.
- AC-S2 The journal is append-only; a resumed run never rewrites prior lines.
- AC-S3 A corrupt trailing journal line is tolerated (truncated replay), and the
  run resumes from the last intact entry.
