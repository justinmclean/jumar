<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-08

Phases 0–5 are complete and merged to `main`. The last five commits landed
repair (AC7.1–7.4), complete (AC8.1–8.4), report+resume (AC9.1–9.5, AC-S1,
AC-S4), verify/judge (AC6.4), recurrence+recurring-complete (AC8.5–8.7), and
verify/manual (AC6.5). The next open item is `lock-single-flight` (Phase 5b).

### Completed (merged to main)

- **models-and-invariants** — all shapes in `specs/03-data-model.md`, Check
  invariants enforced at construction. Closed: AC3.1 (model half), AC3.4.
- **config-and-capabilities** — `config.py`: `gsd.toml` loading, capability
  set, argv allow/deny policy, `is_allowed()`. Closed: config contract.
- **journal-append-and-replay** — `journal.py`: append-only `journal.jsonl`
  with strictly-increasing `seq`, replay, corrupt-line tolerance.
  Closed: AC-S1, AC-S2, AC-S3, AC-S4.
- **ingest-markdown-todos** — `ingest.py`: GFM task list parsing, nested
  authored subtasks, `@key=value` metadata, schedule tokens, stable
  `item_id`, tolerant parse warnings. Closed: AC1.1–AC1.10.
- **select-next-item** — `select.py`: time eligibility, dependency gate, cycle
  detection, overdue/priority/due/file-order sort. Closed: AC2.1–AC2.10.
- **cli-plan-dry-run** — `cli.py`: `gsd plan --dry-run` ingests, selects, and
  prints; `clock.py` as the single injectable `now` source; `run_started`
  journalled with captured `now` and tz. Closed: read-only CLI path;
  clock.py contract from `specs/04`.
- **harness-argv** — `harness.py`: agent-CLI abstraction for all six harnesses,
  pure argv construction, scrubbed environment, hard deny of `git push` / `gh`.
  Closed: AC8.4 at harness level; `specs/04` §Harness contract.
- **decompose-with-required-checks** — `decompose.py`: structured plan request
  via the harness, defensive JSON parse, one retry on any retriable rejection,
  hard rejections (missing check, placeholder statement, plan too long, `judge`
  without `rationale`, cyclic `depends_on`), full plan journalled before return.
  Closed: AC3.1–AC3.6.
- **gate-modes** — `gate.py`: `--dry-run` / `--approve` / `--auto` mode
  dispatch, pre-execution capability refusal naming the subtask and capability,
  `--approve --non-interactive` startup error before ingest.
  Closed: AC4.1–AC4.4.
- **execute-and-verify-command** — `execute.py` + `verify/__init__.py`
  (registry) + `verify/command.py`: one subtask dispatched per execute call,
  wall-clock timeout termination, evidence capture, agent claim stored as a
  claim only; `command` verifier; dummy-kind registry test.
  Closed: AC5.1–AC5.5, AC6.1, AC6.3, AC6.6, AC6.7.
- **verify-file-and-absence** — `verify/file.py`: `file` kind (path exists,
  optional pattern match, hash in evidence) and `absence` kind (path/glob gone),
  both registered in the verifier registry. Closed: AC6.2.
- **repair-bounded** — `repair.py`: up to `max_repairs` retry attempts, failing
  evidence injected into the repair prompt, rejection of any repair that mutates
  its subtask's `check`, terminal state on budget exhaustion, `--halt-on-fail`
  support. Closed: AC7.1–AC7.4.
- **complete-item** — `complete.py`: checkbox flip byte-preserving (only the
  target line changes), optional per-item branch commit with item text as
  subject, no push and no PR ever invoked. Closed: AC8.1–AC8.4.
- **report-and-resume** — `report.py` + `gsd resume <run-id>` CLI command:
  per-run report written to `runs/<run-id>/report.md`, summary to stdout, exit
  status 0 on clean run and 1 on any failure, partial report from an interrupted
  journal (clearly marked), deferred-only runs exit 0 and name the next
  eligibility instant, overdue items listed as overdue even when they completed.
  Resume replays the journal and continues from the first unverified subtask.
  Closed: AC9.1–AC9.5, AC-S1, AC-S4.
- **verify-judge-adversarial** — `verify/judge.py`: fresh context only
  (asserted by inspecting the assembled prompt), default-fail framing, structured
  `{verdict, reason, artefacts_shown}`, unparseable response ⇒ `inconclusive`.
  Closed: AC6.4.
- **verify-manual** — `verify/manual.py`: interactive confirm at the prompt;
  under `--non-interactive` yields `inconclusive`, never auto-pass.
  Closed: AC6.5.
- **clock-and-recurrence** — `recurrence.py`: next-occurrence arithmetic for
  `@every=` rules including DST-gap and DST-fold deterministic resolution.
  Extended `complete.py` to advance a completed recurring item's `@not-before=`
  token in place; missed occurrence advances to the single next occurrence after
  `now`, not a backlog; failed recurring item's schedule left untouched.
  Closed: AC8.5–AC8.7.

---

## Work items (priority order)

Phase ordering comes from `specs/04-technical-plan.md`. AC ids refer to
`specs/02-functional-spec.md`.

---

### Phase 5b — run scheduling

Ship the lock **before** the scheduler. Installing a recurring run before
single-flight exists is how two agents end up editing the same file.

1. **lock-single-flight** — `lock.py`: PID-stamped lock file keyed to the
   todo path, live-lock check exits 0 with `already_running`, stale-lock
   reclaim is journalled and the run proceeds. No scheduler yet — the lock is
   exercised by a test that spawns a fake second run against a live lockfile.
   Wire the lock into `cli.py`'s `gsd run` entry point so it is acquired
   before ingest and released on exit.
   *Validation:* `make check` + `pytest tests/test_lock.py -q` — must include
   the live-lock test (a pre-written lock file whose PID is the current
   process) and the stale-lock reclaim test (a lock file whose recorded PID
   does not exist). Both paths must be journalled.
   *Closes:* AC10.5, AC10.6.
   *Branch slug:* `lock-single-flight`

2. **schedule-os-backends** — `schedule.py`: `gsd schedule add/list/remove/show`
   with `crontab` (Linux/BSD) and `launchd` plist (macOS) backends selected by
   platform; backend overridable in config. Every installed entry is wrapped in
   `# >>> gsd <id> >>>` / `# <<< gsd <id> <<<` markers; `remove` deletes only
   between its own markers. `add` always prints the exact entry before writing.
   The installed command carries absolute `gsd` path, absolute todo path,
   absolute config path, and `--non-interactive`. `schedule show` and `--dry-run`
   print and exit without writing. Invalid cron expressions are rejected with a
   field-level message. Timezone is recorded on the schedule and printed at
   install time. Test against a fake backend that records what would be written,
   plus a round-trip test over a fixture crontab containing unrelated entries
   asserting they survive install and remove byte-identical.
   *Validation:* `make check` + `pytest tests/test_schedule.py -q`.
   *Closes:* AC10.1–AC10.4, AC10.7–AC10.9.
   *Branch slug:* `schedule-os-backends`

---

### Phase 6 — polish

3. **gsd-doctor** — single `gsd doctor` command: config validity, harness
   binary on PATH, allow-list sanity, installed schedule entries readable, todo
   file parseable. Actionable error per failure.
   *Validation:* `make check` + `pytest tests/test_doctor.py -q`.
   *Branch slug:* `gsd-doctor`

4. **ci-and-coverage-floor** — GitHub Actions workflow running `make check`
   on push; per-module coverage floor at 90% enforced once Phase 1 has landed.
   *Validation:* the workflow green on a scratch branch with a coverage
   assertion that fails below 90%.
   *Branch slug:* `ci-and-coverage`

---

## Guardrails (do not re-plan these)

- **Verification is not optional and not deferrable.** No work item may ship a
  stage that performs work ahead of the check that proves it worked.
  `execute-and-verify-command` (now merged) was deliberately two modules in one
  branch for this reason; the same rule applies to any future item that adds an
  execution path.
- **A check may never be weakened to pass.** Not a test, not an AC, not a
  subtask's own `Check` at runtime (that is what AC7.3 exists to enforce).
- **`network` is not a default capability**, and `curl`/`wget`/`ssh`/`scp` stay
  on the standing deny list. Do not plan a work item that relaxes this for
  convenience.
- **No `shell=True`.** Every subprocess is argv. Do not plan a shell-string
  escape hatch.
- **Out of scope for v1** (do not plan): parallel subtask execution, cross-item
  planning, non-Markdown todo inputs, sync with external trackers (Jira, Asana,
  Todoist), a resident daemon, a web UI, Windows Task Scheduler.
- **The product pointing at its own plan** is a milestone, not a dependency.
  Nothing in `src/getstuffdone/` may assume it is being run against this repo.

## Manual follow-ups (USER-side; not loop work items — do not build these)

- Decide whether `todo.md` and `runs/` should be git-ignored in your working
  copy (the shipped `.gitignore` ignores `runs/`, `gsd.toml` and `todo.md` by
  default — remove those lines if you want the list tracked).
- Confirm which agent CLI is on PATH before the first loop run
  (`SPEC_LOOP_AGENT`, default `claude`).
- Run the loop inside a sandbox with no push credentials in the environment.
