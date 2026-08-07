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
todo file  →  1 ingest  →  2 select (time + dependency eligibility)
           →  3 decompose  →  4 gate (dry-run/approve)
           →  5 execute one subtask  →  6 verify it  →  7 repair (bounded)
           →  (loop 5–7 for the next subtask)  →  8 complete item  →  9 report
```

Stages 5–7 are the **inner loop**, run once per subtask. Stages 2–8 are the
**outer loop**, run once per todo item.

Stage 10 sits outside both: it installs the schedule that starts a run at all.

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
  `@max-subtasks=`, and the schedule tokens `@not-before=`, `@due=`, `@every=`.
  Unknown keys are preserved and ignored, never an error.
- **Schedule tokens** parse into the item's `schedule` (`Schedule` in
  `03-data-model.md`):
  - `@not-before=2026-08-11` / `@not-before=2026-08-11T09:00` — the item is not
    eligible before this instant. A bare date means 00:00 local time.
  - `@due=2026-08-14` — advisory deadline. It raises selection priority (stage
    2) and is reported when missed. It never relaxes a check.
  - `@every=weekday` / `@every=1d` / `@every=2w` / `@every=mon,thu` — the item
    recurs: completing it schedules the next occurrence rather than retiring it
    (stage 8).
  Times without a zone are the local zone; the resolved instant is stored in UTC
  and the original string is retained so the line can be rewritten faithfully.
- A schedule token that does not parse is a **hard error for that item** — it is
  marked `blocked` with `bad_schedule` and never executed. Guessing at an
  ambiguous date is exactly the wrong instinct for unattended work.
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
- AC1.8 `@not-before=2026-08-11`, `@due=2026-08-14`, and `@every=weekday` parse
  into the item's `schedule` with the resolved UTC instants and the original
  literals retained.
- AC1.9 An unparseable schedule token (`@due=next tuesday-ish`) marks the item
  `blocked` with `bad_schedule`, names the offending token, and does not abort
  the run.
- AC1.10 A bare date resolves to 00:00 in the configured local zone, and the
  resolved UTC instant reflects that zone — asserted with a fixed `tz` and a
  frozen clock.

**Known gaps**

- Only Markdown task lists in v1. Other formats are out of scope.

---

## Stage 2 — Select

**Where it lives:** `src/getstuffdone/select.py`

**Behaviour & contract**

Chooses the single next item to work, applying two gates in order —
**time eligibility**, then **dependency eligibility** — and then ordering what
survives.

*Time eligibility.* Every selection is evaluated against a single `now`,
captured once per run and journalled, so a long run cannot see time move
underneath it:

- an item with `not_before` in the future is **`deferred`**, not failed, and is
  reported with the instant it becomes eligible;
- a recurring item is eligible when `now >= next_occurrence` (stage 8 maintains
  that instant);
- an item with no schedule is always time-eligible.

*Dependency eligibility.* An item whose `@depends=` names an item that is not
`done` is skipped (not failed) and reported as blocked. Cycles in `@depends` are
detected up front and reported as a configuration error before any work starts.
A dependency that is merely `deferred` blocks its dependant too — the dependant
reports the *dependency's* eligibility instant, so the reason is actionable.

*Ordering.* Among eligible items: **overdue first** (`due < now`, most overdue
first), then explicit `@priority` ascending, then `@due` ascending, then file
order. An item already `done` or `failed` in the journal for this run is never
re-selected.

Being overdue changes only *position in the queue*. It never shortens the plan,
loosens a check, or skips verification.

**Acceptance criteria**

- AC2.1 With no priorities, selection returns items in file order.
- AC2.2 `@priority=1` is selected before an unprioritised earlier item.
- AC2.3 An item depending on an unfinished item is skipped and listed as
  blocked, and the next eligible item is selected instead.
- AC2.4 A dependency cycle raises a configuration error naming the cycle, and
  no item is executed.
- AC2.5 Items marked `done` in the journal are not re-selected on resume.
- AC2.6 With a frozen clock, an item with `not_before` in the future is
  `deferred` and never dispatched; the same item with `not_before` in the past
  is selected.
- AC2.7 An overdue item (`due < now`) is selected ahead of a non-overdue item
  with a numerically better `@priority`.
- AC2.8 `now` is captured once per run: an item that becomes eligible *during* a
  long run is not selected until the next run — asserted with a clock that
  advances between selections.
- AC2.9 An item depending on a `deferred` item is itself blocked, and the report
  names the dependency's eligibility instant, not its own.
- AC2.10 A run in which every item is deferred exits cleanly (status 0) with a
  "nothing eligible" summary and the next eligibility instant.

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

`--approve` and `--non-interactive` are **mutually exclusive** and rejected
together as a configuration error at startup, because the combination has no
safe resolution: auto-approving would defeat the gate, auto-declining would
silently drop every item. A scheduled run (stage 10) is always
`--non-interactive`, so this is a real collision, caught before any work starts
rather than at 3 a.m. mid-run.

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
- AC4.4 `--approve --non-interactive` exits with a configuration error before
  ingest, and no item is executed.

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

- **Non-recurring item:** the todo file's checkbox is flipped `- [ ]` → `- [x]`,
  in place, preserving the rest of the file byte for byte.
- **Recurring item** (`@every=`): the checkbox stays **unchecked** and the line's
  `@not-before=` token is rewritten to the next occurrence, computed from the
  recurrence rule against the completion instant. A recurring item is never
  retired by being done — that is what makes it recurring. If the line had no
  `@not-before=`, one is appended; if it had one, it is replaced in place. The
  completion is recorded in the journal, which is the durable history the file
  no longer carries.
- A missed occurrence does **not** accumulate: if a daily item was not run for
  five days, the next occurrence is the next one after `now`, not five catch-up
  runs. Backlog-of-one is the only sane default for work that is executed rather
  than merely counted.
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
- AC8.5 A completed `@every=` item is left **unchecked** with its `@not-before=`
  advanced to the next occurrence; the rest of the file is byte-identical.
- AC8.6 A recurring item whose occurrence was missed repeatedly advances to the
  single next occurrence after `now`, not to a backlog of missed ones.
- AC8.7 A recurring item that **failed** has its schedule left untouched — a
  failure must not silently defer the item past its next occurrence.

---

## Stage 9 — Report

**Where it lives:** `src/getstuffdone/report.py`

**Behaviour & contract**

Writes a per-run report to `runs/<run-id>/report.md` and prints a summary: items
done / failed / blocked / **deferred** / skipped, and for each failure the
subtask and the check that did not pass. For a run with deferred work it also
prints the **next eligibility instant** across the list, so an unattended
schedule can be sanity-checked from the last report alone. Missed `@due` dates
are listed separately as *overdue*, whether or not the item ran.

Exit status is 0 if every selected item completed (including a run where nothing
was eligible), and 1 if any item failed or was inconclusive. Deferred and
blocked items do not by themselves make a run fail — a scheduled run that had
nothing to do is a success, and must not page anyone.

**Acceptance criteria**

- AC9.1 The report names, for each failed item, the failing subtask index, its
  description, and the check's evidence summary.
- AC9.2 Exit status is 0 on a fully clean run and 1 when any item failed.
- AC9.3 The report is written even when the run is interrupted mid-item, from
  the journal (partial report, clearly marked).
- AC9.4 A run with only deferred items exits 0 and its report names the next
  eligibility instant.
- AC9.5 Overdue items are listed as overdue in the report even when they
  completed successfully.

---

## Stage 10 — Scheduled runs

**Where it lives:** `src/getstuffdone/schedule.py`

**Behaviour & contract**

Installs, lists, and removes a recurring invocation of `gsd run` in the
**operating system's own scheduler**. GetStuffDone does not stay resident; it is
started by cron/launchd/systemd like any other command and exits.

```
gsd schedule add "0 9 * * 1-5" --todo ~/todo.md   # weekdays 09:00 local
gsd schedule list
gsd schedule remove <schedule-id>
gsd schedule show                                  # print the entry, install nothing
```

Contract:

- **Show before install.** Every `add` prints the exact entry it will write and
  the exact command it will run. `--dry-run` (and `schedule show`) prints and
  exits without touching the scheduler.
- **Backend by platform:** `crontab` on Linux/BSD, `launchd` user agent on
  macOS, `systemd --user` timer where available and preferred. The backend is
  selected automatically and overridable in config.
- **Owned entries only.** Entries are delimited by a
  `# >>> gsd <schedule-id> >>>` / `# <<< gsd <schedule-id> <<<` block (or the
  equivalent per backend). `remove` only ever deletes inside its own block, and
  a `list`/`remove` never rewrites a line it did not author. The user's existing
  crontab is not GetStuffDone's to reformat.
- **Absolute everything.** The installed command uses the absolute `gsd` path,
  an absolute `--todo` path, an absolute config path, and `cd`s nowhere
  implicitly — a scheduler's environment is not a login shell's.
- **Always non-interactive.** The installed command carries
  `--non-interactive`, so `manual` checks resolve `inconclusive` (AC6.5) rather
  than hanging a headless process forever, and `--approve` is refused (AC4.4).
- **Single-flight.** A run acquires a lock file for its todo path; a scheduled
  run that finds a live lock exits 0 with `already_running` rather than starting
  a second concurrent agent over the same files. A **stale** lock (owning PID
  gone) is reclaimed with a journalled note.
- **Output goes to the journal, not to mail.** Scheduled runs write
  `runs/<run-id>/` as usual; the installed entry redirects stdout/stderr to a
  log path under the run directory. GetStuffDone sends nothing outward.

Timezone: cron expressions are interpreted in the **local** zone by the backend.
The resolved zone is recorded with the schedule and printed on `add`, because a
schedule that silently means UTC is a schedule that fires at the wrong time for
half the year.

**Acceptance criteria**

- AC10.1 `schedule add --dry-run` prints the exact entry and installs nothing —
  asserted against a fake backend that fails the test if it is written to.
- AC10.2 An installed entry is wrapped in its `gsd <schedule-id>` markers, and
  `remove` deletes only the lines between them, leaving unrelated user entries
  byte-identical.
- AC10.3 The installed command contains absolute paths for `gsd`, the todo file,
  and the config, and carries `--non-interactive`.
- AC10.4 An invalid cron expression is rejected with a message naming the field,
  and nothing is installed.
- AC10.5 A second run against a live lock exits 0 with `already_running` and
  dispatches no agent call.
- AC10.6 A stale lock (recorded PID not running) is reclaimed, the reclaim is
  journalled, and the run proceeds.
- AC10.7 `schedule list` reports only gsd-owned entries, with their id, cron
  expression, resolved timezone, and target todo path.
- AC10.8 Removing an id that does not exist is a clean non-zero error that
  modifies nothing.
- AC10.9 The resolved timezone is recorded on the schedule and printed at
  install time.

**Known gaps**

- No Windows Task Scheduler backend in v1.
- The scheduler is not consulted at run time: `gsd run` behaves identically
  whether a human or cron started it. That is deliberate — one code path.

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
- AC-S4 The run's single `now` and its resolved timezone are journalled in
  `run_started`, and a resumed run reuses the original `now` for eligibility
  rather than re-reading the clock — a resume must not change which items were
  eligible.
