<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-07

Phase 0 (skeleton) and Phase 1 (ingest) are complete. The `select-next-item`
branch carries Stage 2 select (AC2.1–AC2.10) and is 1 commit ahead of main.
Next open item is `cli-plan-dry-run`.

### Completed (merged to main)

- **models-and-invariants** — all shapes in `specs/03-data-model.md`, Check
  invariants enforced at construction. Closed: AC3.1 (model half), AC3.4.
- **config-and-capabilities** — `config.py`: `gsd.toml` loading, capability
  set, argv allow/deny policy, `is_allowed()`. Closed: config contract.
- **journal-append-and-replay** — `journal.py`: append-only `journal.jsonl`
  with strictly-increasing `seq`, replay, corrupt-line tolerance. Closed:
  AC-S1, AC-S2, AC-S3, AC-S4.
- **ingest-markdown-todos** — `ingest.py`: GFM task list parsing, nested
  authored subtasks, `@key=value` metadata, schedule tokens, stable
  `item_id`, tolerant parse warnings. Closed: AC1.1–AC1.10.

### In-flight (local branch — do not re-plan)

- **select-next-item** — `select.py`: time eligibility, dependency gate, cycle
  detection, overdue/priority/due/file-order sort. Closed: AC2.1–AC2.10.
  Branch is 1 commit ahead of main.

## Work items (priority order)

Phase ordering comes from `specs/04-technical-plan.md`. AC ids refer to
`specs/02-functional-spec.md`.

---

### Phase 1 (finish) — read-only path

1. **cli-plan-dry-run** — `cli.py`: `gsd plan --dry-run` ingests the todo
   file, selects the next eligible item, and prints the result, exiting 0
   with no agent call. Introduce `clock.py` here as the single injectable
   `now` source (`capture_now(config) -> datetime`), so no other module ever
   reads the wall clock directly. Journal a `run_started` event with the
   captured `now` and resolved tz.
   *Validation:* `make check` + `pytest tests/test_cli.py -q` — must assert
   that `gsd plan --dry-run` exits 0, produces output, makes no harness
   calls, and that `clock.py` is the only module that reads the wall clock.
   *Closes:* read-only CLI path; clock.py contract from `specs/04`.

---

### Phase 2 — decompose + gate

2. **harness-argv** — `harness.py`: the agent-CLI abstraction for `claude` /
   `codex` / `cursor` / `gemini` / `opencode` / `kiro`, with pure and
   fixture-tested argv construction, scrubbed environment, and the hard deny
   of `git push` / `gh`. Capability enforcement is the single choke point
   here — no stage can bypass it.
   *Validation:* `make check` + `pytest tests/test_harness_argv.py -q` — must
   include a fake-agent test that records argv and a test that fails if
   `git push` or `gh` can reach it.
   *Closes:* AC8.4 at the harness level; `specs/04` §Harness contract.

3. **decompose-with-required-checks** — `decompose.py`: structured plan
   request via the harness, defensive JSON parse, one retry on any rejection,
   hard rejections (missing `check`, placeholder statement, plan too long,
   `judge` without `rationale`, cyclic `depends_on` within a plan).
   *Validation:* `make check` + `pytest tests/test_decompose.py -q` against
   the fake harness — one negative test per rejection path.
   *Closes:* AC3.1–AC3.6.

4. **gate-modes** — `gate.py`: `--dry-run` / `--approve` / `--auto` mode
   dispatch, pre-execution capability refusal naming the subtask and
   capability, and the startup check that rejects `--approve` combined with
   `--non-interactive` before ingest runs. The `--non-interactive` flag is
   introduced here as a CLI-level concept.
   *Validation:* `make check` + `pytest tests/test_gate.py -q` — must include
   a test for each of AC4.1–AC4.4, including the `--approve
   --non-interactive` startup error with a mock that asserts ingest is never
   called.
   *Closes:* AC4.1–AC4.4.

---

### Phase 3 — the inner loop (execute and verify land together)

5. **execute-and-verify-command** — `execute.py` **and**
   `verify/__init__.py` (registry) **and** `verify/command.py`, in one
   branch. One subtask dispatched per execute call, wall-clock timeout
   termination, evidence capture, agent claim stored as a claim only. The
   `command` verifier exits the golden path; the registry makes adding new
   kinds a one-module operation. Include a golden-journal test asserting
   `attempt_started` → `attempt_finished` → `verification` order before the
   next subtask starts.
   **Do not split this item** — shipping execute without a verifier is
   forbidden by AGENTS.md.
   *Validation:* `make check` + `pytest tests/test_execute.py
   tests/test_verify_command.py -q` plus the golden-journal ordering test.
   *Closes:* AC5.1–AC5.5, AC6.1, AC6.3, AC6.6, AC6.7.

6. **verify-file-and-absence** — `verify/file.py`: `file` kind (path exists,
   optional pattern match, hash in evidence) and `absence` kind (path/glob is
   gone). Both are registered in the verifier registry.
   *Validation:* `make check` + `pytest tests/test_verify_file.py -q`.
   *Closes:* AC6.2.

7. **repair-bounded** — `repair.py`: up to `max_repairs` retry attempts,
   failing evidence injected into the repair prompt, rejection of any repair
   that mutates its subtask's `check` (original check re-run), correct
   terminal state on budget exhaustion, `--halt-on-fail` support.
   *Validation:* `make check` + `pytest tests/test_repair.py -q` — must
   include the check-mutation rejection test.
   *Closes:* AC7.1–AC7.4.

---

### Phase 4 — completion

8. **complete-item** — `complete.py`: checkbox flip byte-preserving (only the
   target line changes), optional per-item branch commit with item text as
   subject, no push and no PR ever invoked. Non-recurring items only in this
   item; recurring item support comes in Phase 4b.
   *Validation:* `make check` + `pytest tests/test_complete.py -q` — must
   include a byte-diff assertion and a temp-git-repo branch assertion.
   *Closes:* AC8.1–AC8.4.

9. **report-and-resume** — `report.py` + `gsd resume <run-id>` CLI command:
   per-run report written to `runs/<run-id>/report.md`, summary to stdout,
   exit status 0 on clean run and 1 on any failure, partial report from an
   interrupted journal (clearly marked). Deferred-only runs exit 0 and name
   the next eligibility instant. Overdue items are listed as overdue even
   when they completed. Resume replays the journal and continues from the
   first unverified subtask.
   *Validation:* `make check` + `pytest tests/test_report.py
   tests/test_resume.py -q`.
   *Closes:* AC9.1–AC9.5.

---

### Phase 4b — item scheduling

10. **clock-and-recurrence** — `recurrence.py`: next-occurrence arithmetic
    for `@every=` rules (weekday, interval+unit, named-DOW), including
    DST-gap and DST-fold deterministic resolution. Extend `complete.py` to
    advance a completed recurring item's `@not-before=` token in place
    (rewrite only that token, leave the rest of the line byte-exact); a
    missed occurrence advances to the single next occurrence after `now`, not
    a backlog. A failed recurring item's schedule is left untouched.
    *Validation:* `make check` + `pytest tests/test_recurrence.py
    tests/test_complete.py -q` — must include DST-gap, DST-fold, and
    missed-occurrence tests. A test asserts `complete.py` on a recurring item
    leaves the checkbox unchecked.
    *Closes:* AC8.5–AC8.7.

---

### Phase 5 — remaining verifiers

11. **verify-judge-adversarial** — `verify/judge.py`: fresh context only
    (asserted by inspecting the assembled prompt), default-fail framing,
    structured `{verdict, reason, artefacts_shown}`, unparseable response ⇒
    `inconclusive`.
    *Validation:* `make check` + `pytest tests/test_verify_judge.py -q` —
    must include a test that inspects the assembled verifier prompt and asserts
    no fragment of the executor's transcript is present.
    *Closes:* AC6.4.

12. **verify-manual** — `verify/manual.py`: interactive confirm at the
    prompt; under `--non-interactive` yields `inconclusive`, never auto-pass.
    *Validation:* `make check` + `pytest tests/test_verify_manual.py -q`.
    *Closes:* AC6.5.

---

### Phase 5b — run scheduling

Ship the lock **before** the scheduler. Installing a recurring run before
single-flight exists is how two agents end up editing the same file.

13. **lock-single-flight** — `lock.py`: PID-stamped lock file keyed to the
    todo path, live-lock check exits 0 with `already_running`, stale-lock
    reclaim is journalled and the run proceeds. No scheduler yet — the lock is
    exercised by a test that spawns a fake second run against a live lockfile.
    *Validation:* `make check` + `pytest tests/test_lock.py -q` — must include
    the stale-lock reclaim test (fake PID that does not exist).
    *Closes:* AC10.5, AC10.6.

14. **schedule-os-backends** — `schedule.py`: `gsd schedule add/list/remove/show`
    with `crontab` (Linux/BSD) and `launchd` plist (macOS) backends selected
    by platform; backend overridable in config. Every installed entry is wrapped
    in `# >>> gsd <id> >>>` / `# <<< gsd <id> <<<` markers; `remove` deletes
    only between its own markers. `add` always prints the exact entry before
    writing. The installed command carries absolute `gsd` path, absolute todo
    path, absolute config path, and `--non-interactive`. `schedule show` and
    `--dry-run` print and exit without writing. Invalid cron expressions are
    rejected with a field-level message. Timezone is recorded on the schedule
    and printed at install time. Test against a fake backend that records what
    would be written, plus a round-trip test over a fixture crontab containing
    unrelated entries asserting they survive install and remove byte-identical.
    *Validation:* `make check` + `pytest tests/test_schedule.py -q`.
    *Closes:* AC10.1–AC10.4, AC10.7–AC10.9.

---

### Phase 6 — polish

15. **gsd-doctor** — single `gsd doctor` command: config validity, harness
    binary on PATH, allow-list sanity, installed schedule entries readable, todo
    file parseable. Actionable error per failure.
    *Validation:* `make check` + `pytest tests/test_doctor.py -q`.

16. **ci-and-coverage-floor** — GitHub Actions workflow running `make check`
    on push; per-module coverage floor at 90% enforced once Phase 1 has landed.
    *Validation:* the workflow green on a scratch branch with a coverage
    assertion that fails below 90%.

---

## Guardrails (do not re-plan these)

- **Verification is not optional and not deferrable.** No work item may ship a
  stage that performs work ahead of the check that proves it worked. Item 5 is
  deliberately two modules in one branch for this reason.
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
- Merge the `select-next-item` branch to main when ready before building the
  next item.
