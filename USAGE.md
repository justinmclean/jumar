<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Using GetStuffDone

Worked examples, command reference, config reference. Every transcript below is
**real output** from the current build, not illustration. Where a command is not
yet wired, this document says so rather than showing what it will one day print.

See [README.md](README.md) for what the tool is and why. See
[`specs/02-functional-spec.md`](specs/02-functional-spec.md) for the contract
each stage must satisfy.

---

## 1. Set up a todo file

`todo.md` is git-ignored by default, so your real list never lands in a commit.

```bash
cp todo.example.md todo.md
```

```markdown
# My list

Context lines like this one are attached to the item below them and are passed
to the agent as background — they are never treated as work.

- [ ] Add a --json flag to the export script @priority=1 @capability=write_fs
- [ ] Update the README install section @depends=export-json
      - [ ] Rewrite the install steps for the new flag
      - [ ] Check every command in the README actually runs
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [x] This one is already done and will be skipped
```

---

## 2. See what it would do — `gsd plan --dry-run`

The safest command in the tool. It ingests the file, applies both eligibility
gates, and prints the decision. **No agent is called and nothing is executed.**

```console
$ gsd plan --dry-run
Run:   9daec9eb-ae52-4223-b6c8-e450fafd52cd
Todo:  todo.md  (4 pending)
Now:   2026-08-08T00:51:11.729401+00:00

Selected:  Add a --json flag to the export script
  id:           add-a-json-flag-to-the-export-script-a67187c5
  capabilities: read_fs, run_commands, write_fs

Deferred (1):
  - 'Draft the quarterly summary'  [eligible at 2026-09-01T00:00:00+00:00]

Blocked (1):
  - 'Update the README install section'  [depends on unfinished export-json]
```

Reading that output:

- **Selected** — the one item that would be worked. Exactly one, always.
- **Deferred** — time-ineligible (`@not-before=` in the future). Not a failure;
  the instant it becomes eligible is shown.
- **Blocked** — dependency-ineligible (`@depends=` on an unfinished item).
- **Now** — the run's single eligibility instant, captured once and journalled.
  A long run cannot see time move underneath it.

Point it at another file with `--todo`:

```bash
gsd plan --dry-run --todo ~/work/todo.md
```

Every invocation writes `runs/<run-id>/journal.jsonl`, even a dry run. The
journal is the audit trail, and it is what `resume` and `report` read.

### Bad schedule tokens block one item, not the run

An unparseable date is refused rather than guessed at — guessing is exactly the
wrong instinct for unattended work:

```console
$ gsd plan --dry-run --todo bad.md
warning: Line 1: bad schedule token: @due='next': Cannot parse date/datetime: 'next'
Run:   64fcaeff-0eca-4a7f-ba36-295b6a956e3d
Todo:  bad.md  (1 pending)
Now:   2026-08-08T00:51:20.666894+00:00

Selected:  Fine item
  id:           fine-item-334aeee7
  capabilities: read_fs, run_commands, write_fs
```

The malformed item is dropped from consideration; the rest of the file still
parses and the run still exits 0.

### Errors are actionable, not tracebacks

```console
$ gsd plan --dry-run --todo nope.md
error: Todo file not found: nope.md
$ echo $?
1
```

---

## 3. Run an item — `gsd run`

**Not yet wired.** The flag validation is live; the pipeline behind it is not:

```console
$ gsd run
gsd run: full pipeline not yet implemented — see IMPLEMENTATION_PLAN.md
$ echo $?
2
```

The startup gate *does* work, and is worth knowing about, because it is the one
flag combination with no safe resolution — auto-approving would defeat the gate,
auto-declining would silently drop every item:

```console
$ gsd run --approve --non-interactive
error: --approve and --non-interactive are mutually exclusive: --approve waits
for a human response, but --non-interactive forbids blocking reads. Remove one
of these flags.
$ echo $?
2
```

Planned modes, once wired (`specs/02-functional-spec.md` §Stage 4):

| Flag | Behaviour |
|---|---|
| *(none)* | `auto` — plan, then execute straight through. |
| `--dry-run` | Print the plan and its checks, journal it, execute nothing. |
| `--approve` | Print the plan and wait for `y/n` before executing. |
| `--non-interactive` | Never block on a prompt. Required for scheduled runs; makes `manual` checks resolve `inconclusive` rather than hanging. |

Until then, `gsd resume` is the working path through the full loop — see §5.

---

## 4. Read a run — `gsd report`

```console
$ gsd report demo-run
Run: 1 failed

Failed: Add a --json flag to the export script
  Reason: check_failed
  Subtask 2: Document the flag in the README
  Check (file): README documents --json
  Evidence: pattern '--json' not found in README.md

Report written to: runs/demo-run/report.md
$ echo $?
1
```

That is the shape of every failure report: **which subtask, which check, what
the evidence was**. Not "the agent reported a problem".

The written `runs/<run-id>/report.md`:

```markdown
# Run Report: demo-run

- **Started:** 2026-08-08T09:00:00+00:00
- **Timezone:** Australia/Sydney
- **Todo file:** todo.md
- **Mode:** auto

## Summary

| Status | Count |
|--------|-------|
| Done | 0 |
| Failed | 1 |

## Failed Items

### Add a --json flag to the export script
- Item ID: `add-a-json-flag-to-the-export-script-a67187c5`
- Failure: `check_failed`
- Failed at subtask 2: Document the flag in the README
  - Check (`file`): README documents --json
  - Evidence: pattern '--json' not found in README.md
```

**Exit status is the contract:** `0` if every selected item completed —
*including a run where nothing was eligible* — and `1` if any item failed or was
inconclusive. Deferred and blocked items do not fail a run. A scheduled run with
nothing to do is a success and must not page anyone.

```bash
gsd report <run-id> --runs-dir /path/to/runs   # non-default runs directory
```

---

## 5. Resume an interrupted run — `gsd resume`

This is currently the only command that drives the full execute → verify →
repair loop. It replays `runs/<run-id>/journal.jsonl` and continues from the
first subtask **without a `passed` verdict**.

```bash
gsd resume 9daec9eb-ae52-4223-b6c8-e450fafd52cd
```

Two properties worth understanding, because they are what make resume safe:

- **Verified work is never redone.** A subtask with a `passed` verdict in the
  journal is skipped, not re-executed. Side effects are not repeated.
- **Eligibility does not shift.** Resume reuses the *original* `now` from the
  journal's `run_started` event rather than reading the clock again. An item
  that was deferred when the run started stays deferred on resume — otherwise
  the resumed run would be a different run.

Resume writes `report.md` on completion and returns the same exit-status
contract as `gsd report`.

---

## 6. Scheduling

Two independent mechanisms. Keeping them separate matters.

### Item eligibility — works today

Expressed in the todo line, honoured by `select`:

```markdown
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [ ] Reconcile invoices @every=2w @priority=1
```

| Token | Accepted forms |
|---|---|
| `@not-before=` | `2026-09-01`, `2026-09-01T09:00` |
| `@due=` | same |
| `@every=` | `weekday`, `1d`, `2w`, `mon,thu` |

A bare date resolves to 00:00 in the configured timezone. Everything downstream
is timezone-aware UTC.

**Ordering among eligible items:** overdue first (most overdue first), then
`@priority` ascending, then `@due` ascending, then file order.

**Overdue changes queue position and nothing else.** It never shortens a plan,
loosens a check, or skips verification — enforced by a test asserting `due` is
unread by `decompose`, `execute`, `verify` and `repair`.

**Recurrence on completion:** a completed `@every=` item stays *unchecked* with
its `@not-before=` advanced in place; the rest of the line is byte-identical. A
missed occurrence advances to the single next occurrence after `now`, never a
backlog of catch-up runs. A *failed* recurring item's schedule is left untouched.

### Recurring runs — not yet built

`gsd schedule` is a stub. When built it will write a `cron`/`launchd` entry that
invokes `gsd run --non-interactive` and exit — there is no resident daemon:

```bash
gsd schedule add "0 9 * * 1-5" --todo ~/todo.md --dry-run   # print, install nothing
gsd schedule add "0 9 * * 1-5" --todo ~/todo.md
gsd schedule list
gsd schedule remove <schedule-id>
```

**Do not hand-roll a cron entry for `gsd run` in the meantime.** `lock.py`
(single-flight) is also unbuilt, so two overlapping firings would run two agents
over the same files with nothing to stop them. The plan ships the lock before
the scheduler for exactly this reason.

---

## 7. Configuration

`gsd.toml` in the working directory, or a `[tool.gsd]` table in
`pyproject.toml`. CLI flags override config. Current effective defaults:

```toml
[gsd]
todo_path           = "todo.md"
timezone            = "Australia/Sydney"   # defaults to the system zone
max_subtasks        = 12
max_repairs         = 2
subtask_timeout_s   = 900
halt_on_fail        = false
commit_on_complete  = false
capabilities        = ["read_fs", "write_fs", "run_commands"]   # network NOT default
evidence_head_bytes = 4000

[gsd.harness]
agent = "claude"       # claude | codex | cursor | gemini | opencode | kiro
model = "sonnet"

[gsd.commands]
allow = ["python3", "pytest", "ruff", "git", "make"]
deny  = ["curl", "wget", "ssh", "scp"]
```

Notes that matter:

- **`deny` beats `allow`.** An `argv[0]` outside `allow` is refused *before* the
  process is spawned, with `capability_denied`.
- **`network` is not granted by default.** Add it per item with
  `@capability=network` when a task genuinely needs it, rather than globally.
- **`commit_on_complete`** commits a finished item on a per-item branch. It
  never pushes and never opens a PR.
- **`timezone`** resolves bare dates like `@due=2026-09-05`. Set it explicitly if
  you don't want the system zone.

---

## 8. Check kinds

Every subtask carries exactly one `Check`. A subtask whose plan has no check is
rejected at planning time — the decomposition is retried once, then the item
fails with `unverifiable_plan`.

| Kind | Passes when | Evidence recorded |
|---|---|---|
| `command` | argv exits with the expected status (default 0) | argv, exit code, captured stdout/stderr |
| `file` | path exists and matches the required pattern | path, size, sha256, matched excerpt |
| `absence` | path or glob is gone | path, resolved glob |
| `judge` | an independent agent returns `pass` | verdict, reason, artefacts shown |
| `manual` | a human confirms at the prompt | response, timestamp |

Rules worth knowing before you write a `@check=` by hand:

- A `command` check is **argv, never a shell string**. No metacharacter
  interpolation, anywhere.
- `judge` requires a `rationale` explaining why no executable check is possible.
  It is prompted adversarially — its default answer is fail, and "looks
  reasonable" is explicitly not evidence. An unparseable response is
  `inconclusive`, not a pass.
- `manual` under `--non-interactive` yields `inconclusive`, never an auto-pass.
- A check that **cannot run** — missing binary, unreachable path — is
  `inconclusive`. That is not a pass, and it routes to repair like a failure.

Adding a sixth kind requires only a new module plus one `register()` call in
`verify/__init__.py`; nothing in `execute.py` changes. That claim is now backed
by four real additions rather than one.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: Todo file not found: X` | `todo_path` wrong, or you're in the wrong directory | `gsd plan --dry-run --todo /absolute/path` |
| `warning: bad schedule token` | Unparseable `@not-before=` / `@due=` / `@every=` | Use `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`; that item is skipped until fixed |
| `Nothing eligible to work on.` | Everything deferred, blocked, or done | The printed *next eligible* instant tells you when to come back |
| `error: --approve and --non-interactive are mutually exclusive` | Both flags passed | Drop one; scheduled runs must use `--non-interactive` |
| `capability_denied` | Subtask wants authority the item wasn't granted | Add `@capability=` to the item, or fix the plan |
| `unverifiable_plan` | Decomposition produced a subtask with no real check | Usually a too-vague todo line — split it, or supply your own indented subtasks |
| `plan_too_long` | Decomposition exceeded `max_subtasks` | The item is mis-sized. Split it. Raising the cap is treating the symptom |
| `ImportError: cannot import name 'UTC'` | Python 3.10 or older | Needs 3.11+ |
| `gsd: command not found` | Not installed | `make install` |

Inspect any run directly — the journal is append-only JSON lines:

```bash
ls runs/
python3 -m json.tool < runs/<run-id>/journal.jsonl   # per-line
cat runs/<run-id>/report.md
```

---

## 10. Command reference

```
gsd [--version] [--help]
gsd plan   [--dry-run] [--todo PATH]
gsd run    [--dry-run | --approve] [--non-interactive] [--todo PATH]
gsd resume RUN_ID [--runs-dir DIR]
gsd report RUN_ID [--runs-dir DIR]
gsd schedule                                   # stub
gsd doctor                                     # stub
```

Exit statuses: `0` success (including nothing eligible), `1` a run failed or was
inconclusive, `2` a usage or not-implemented error.

`gsd plan` without `--dry-run` currently exits 2 — the full plan path is wired
to `--dry-run` only.
