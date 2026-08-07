<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->
---
status: proposed
---

# 03 — Data Model

Canonical shapes. Implemented as frozen dataclasses (or pydantic models) in
`src/getstuffdone/models.py`. Anything serialised to the journal is
JSON-round-trippable: every field is a primitive, a list, a dict, or another
model here.

## Enumerations

```
ItemStatus    = pending | in_progress | done | failed | blocked
              | skipped_by_human | inconclusive | deferred
SubtaskStatus = pending | running | passed | failed | inconclusive | skipped
CheckKind     = command | file | absence | judge | manual
Verdict       = passed | failed | inconclusive
Capability    = read_fs | write_fs | run_commands | network | git_commit
FailureCode   = unverifiable_plan | plan_too_long | invalid_plan
              | capability_denied | timed_out | check_failed
              | repairs_exhausted | harness_error | dependency_blocked
              | bad_schedule | already_running
RecurUnit     = day | week | month | weekday | dow      # dow = named weekdays
ScheduleBackend = cron | launchd | systemd
```

`Capability` is deliberately coarse. Fine-grained allow-lists (which commands,
which hosts) live in config, not in the model.

## TodoItem

Produced by stage 1 (ingest).

| field | type | notes |
|---|---|---|
| `item_id` | str | stable: `@id=` or `slug(text)-<8 hex of sha256(text)>` |
| `text` | str | the todo line, metadata tokens stripped |
| `raw_line` | str | the original line, byte-exact (needed to rewrite it) |
| `line_no` | int | 1-based line number in the todo file |
| `status` | ItemStatus | `done` if the source line was `- [x]` |
| `context` | list[str] | preceding prose/headings, in order |
| `authored_subtasks` | list[str] | from an indented task list; may be empty |
| `meta` | dict[str, str] | parsed `@key=value` tokens, unknown keys kept |
| `priority` | int \| None | from `@priority` |
| `depends` | list[str] | item_ids from `@depends` (comma-separated) |
| `capabilities` | set[Capability] | granted set: config default ∪ `@capability` |
| `schedule` | Schedule \| None | parsed `@not-before` / `@due` / `@every` |

`decomposition` is derived: `authored` when `authored_subtasks` is non-empty,
else `model`.

## Schedule

Item-level **eligibility**, parsed by stage 1, consumed by stage 2 and
maintained by stage 8. Not a calendar entry: it says when an item may be worked,
not when a human should be somewhere.

| field | type | notes |
|---|---|---|
| `not_before` | str \| None | ISO-8601 **UTC** instant; item is ineligible before it |
| `not_before_literal` | str \| None | the original `@not-before=` text, kept so the line can be rewritten faithfully |
| `due` | str \| None | ISO-8601 UTC; advisory — affects ordering and reporting only |
| `due_literal` | str \| None | original `@due=` text |
| `recur` | Recurrence \| None | from `@every=` |
| `tz` | str | IANA zone used to resolve bare dates (e.g. `Australia/Sydney`) |

`Recurrence` = `{unit: RecurUnit, interval: int, days: list[str]}` — `@every=2w`
is `{week, 2, []}`, `@every=weekday` is `{weekday, 1, []}`, `@every=mon,thu` is
`{dow, 1, ["mon","thu"]}`.

Invariants (enforced in `models.py`, tested):

- `not_before` and `due`, when present, are timezone-aware UTC instants — a
  naive datetime never enters the model.
- `due` is advisory. **Nothing in the codebase may branch on `due` other than
  ordering (stage 2) and reporting (stage 9).** A lateness check that skipped
  verification would invert the product's whole premise; a test asserts `due` is
  not read by `decompose`, `execute`, `verify`, or `repair`.
- `recur` requires `interval >= 1`; `unit=dow` requires a non-empty `days`.
- A `Schedule` that fails to parse is never constructed — stage 1 raises
  `bad_schedule` for that item instead of building a partial one.

## Check

The executable proof a subtask worked. Produced by stage 3, consumed by stage 6.

| field | type | notes |
|---|---|---|
| `kind` | CheckKind | |
| `statement` | str | human-readable acceptance sentence, always required |
| `command` | list[str] \| None | argv for `kind=command`; never a shell string |
| `expect_status` | int | default 0 |
| `expect_stdout` | str \| None | optional regex the output must match |
| `path` | str \| None | for `file` / `absence`; repo-relative |
| `pattern` | str \| None | regex the file's contents must match (`file`) |
| `rationale` | str \| None | **required** when `kind=judge`: why no executable check is possible |
| `timeout_s` | int | default 300 |

Invariants (enforced in `models.py`, tested):

- `kind=command` ⇒ `command` non-empty.
- `kind in {file, absence}` ⇒ `path` non-empty.
- `kind=judge` ⇒ `rationale` non-empty.
- `statement` is non-empty and is not one of the placeholder strings
  (`""`, `"n/a"`, `"none"`, `"TODO"`, `"verify manually"`) — the anti-"trust me"
  guard from AC3.1.
- A `command` is argv, never `shell=True`. No shell metacharacter interpolation.

## Subtask

| field | type | notes |
|---|---|---|
| `subtask_id` | str | `<item_id>#<index>` |
| `index` | int | 0-based position in the plan |
| `description` | str | one imperative step |
| `check` | Check | required — a subtask without one is never constructed |
| `capabilities` | set[Capability] | must be a subset of the item's granted set |
| `depends_on` | list[int] | indices within the same plan; must form a DAG |
| `status` | SubtaskStatus | |
| `attempts` | list[Attempt] | in order, including repairs |

## Plan

| field | type | notes |
|---|---|---|
| `item_id` | str | |
| `subtasks` | list[Subtask] | ordered; `len <= max_subtasks` |
| `source` | str | `authored` \| `model` |
| `created_at` | str | ISO-8601 UTC |
| `harness` | HarnessInfo | agent/model that produced it |

## Attempt

One execution of one subtask (initial or repair).

| field | type | notes |
|---|---|---|
| `attempt_no` | int | 0 = initial, 1..n = repairs |
| `started_at` / `finished_at` | str | ISO-8601 UTC |
| `harness` | HarnessInfo | |
| `agent_claim` | str \| None | the agent's own summary — a claim, never a verdict |
| `transcript_path` | str | artefact file under `runs/<run-id>/artifacts/` |
| `exit_status` | int \| None | harness process status |
| `files_touched` | list[str] | best-effort, from git status or mtime scan |
| `error` | str \| None | harness-level failure |

## VerificationResult

| field | type | notes |
|---|---|---|
| `subtask_id` | str | |
| `attempt_no` | int | which attempt this verdict judges |
| `verdict` | Verdict | |
| `kind` | CheckKind | echoed for reporting |
| `evidence` | dict | kind-specific; see below |
| `summary` | str | one line, human-facing |
| `evidence_path` | str \| None | full untruncated output artefact |
| `checked_at` | str | ISO-8601 UTC |

Evidence by kind:

- `command`: `{argv, exit_status, stdout_head, stderr_head, duration_s}`
- `file`: `{path, exists, size, sha256, matched_excerpt}`
- `absence`: `{path, resolved, still_present}`
- `judge`: `{verdict, reason, artefacts_shown}`
- `manual`: `{response, answered_at}`

## Run

| field | type | notes |
|---|---|---|
| `run_id` | str | `<UTC timestamp>-<6 random chars>` |
| `todo_path` | str | |
| `mode` | str | `auto` \| `dry-run` \| `approve` |
| `interactive` | bool | false for scheduled runs |
| `trigger` | str | `human` \| `schedule:<schedule-id>` |
| `now` | str | the single eligibility instant for the whole run (ISO-8601 UTC) |
| `tz` | str | resolved local zone, recorded so a report is interpretable later |
| `config` | dict | resolved effective config, for reproducibility |
| `started_at` / `finished_at` | str | |
| `items` | list[ItemResult] | |
| `warnings` | list[str] | ingest parse warnings etc. |

`ItemResult` = `{item_id, status, plan, failure_code, failed_subtask_index,
verifications, eligible_at, was_overdue, next_occurrence}` — `eligible_at` is
set on a `deferred` item, `next_occurrence` on a completed recurring one.

## ScheduleEntry

A recurring invocation of `gsd run` installed in the OS scheduler (stage 10).
GetStuffDone owns the record; the scheduler owns the firing.

| field | type | notes |
|---|---|---|
| `schedule_id` | str | short slug; also the marker used to delimit the owned block |
| `cron` | str | 5-field expression, interpreted in `tz` |
| `tz` | str | resolved IANA zone, recorded at install time |
| `backend` | ScheduleBackend | |
| `todo_path` | str | absolute |
| `config_path` | str \| None | absolute |
| `command` | list[str] | the exact argv installed, absolute `gsd`, always including `--non-interactive` |
| `log_path` | str | where the entry redirects stdout/stderr |
| `installed_at` | str | ISO-8601 UTC |

Invariants:

- `command[0]` is absolute; `todo_path` and `config_path` are absolute.
- `--non-interactive` is present in `command`; `--approve` is absent.
- `schedule_id` matches `[a-z0-9-]{1,32}` — it lands in a crontab comment marker
  and must not be able to break out of it.

## RunLock

Single-flight guard for a todo path (AC10.5, AC10.6).

`{todo_path, run_id, pid, hostname, acquired_at}` — written to
`runs/.lock-<hash of todo_path>`. A lock whose `pid` is not live is **stale** and
may be reclaimed, with the reclaim journalled.

## HarnessInfo

`{agent, model, harness, invoked_as}` — recorded on every plan, attempt, and
judge verdict so a journal is reproducible and attributable.

## Journal record

One JSON object per line in `runs/<run-id>/journal.jsonl`:

```json
{"ts": "...", "seq": 12, "event": "verification", "run_id": "...",
 "item_id": "...", "subtask_id": "...", "payload": { ... }}
```

`event` ∈ `run_started | item_selected | item_deferred | plan_created |
plan_rejected | gate_decision | attempt_started | attempt_finished |
verification | repair_started | item_completed | item_failed |
schedule_advanced | lock_reclaimed | run_finished`.

`run_started` carries the run's `now`, `tz`, `trigger`, and `interactive` flag —
which is what makes an eligibility decision reproducible after the fact, and what
a resume replays instead of re-reading the clock (AC-S4).

Invariants:

- Append-only. `seq` strictly increases within a run.
- Every `attempt_finished` is followed by a `verification` for the same
  `subtask_id` before any other `attempt_started` — this is the machine-checkable
  form of "one subtask at a time, verified before the next".
- Payloads embed the models above verbatim; the journal is the source of truth
  for resume and for the report.

## Config

`gsd.toml` (or `[tool.gsd]` in `pyproject.toml`), resolved config ⊕ CLI flags:

```toml
[gsd]
todo_path       = "todo.md"
timezone        = "Australia/Sydney"   # resolves bare dates; defaults to system zone
max_subtasks    = 12
max_repairs     = 2
subtask_timeout_s = 900
halt_on_fail    = false
commit_on_complete = false
capabilities    = ["read_fs", "write_fs", "run_commands"]   # network NOT default
evidence_head_bytes = 4000

[gsd.harness]
agent = "claude"
model = "sonnet"

[gsd.schedule]
backend    = "auto"        # auto | cron | launchd | systemd
log_dir    = "runs/logs"
lock_dir   = "runs"

[gsd.commands]
allow = ["python3", "pytest", "ruff", "git", "make"]        # argv[0] allow-list
deny  = ["curl", "wget", "ssh", "scp"]
```

`deny` wins over `allow`. An argv[0] outside `allow` is refused at execution
time with `capability_denied`, before the process is spawned.
