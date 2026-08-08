<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-08

All phases through Phase 6 are fully merged to main (Phases 0–6 complete).
All three remaining work items (Phases 7–8) are built and live on local
branches — none have been pushed or merged yet. There are no open spec gaps
beyond the three USER-side spec amendments noted in the manual follow-ups
section below.

The build beat has nothing to pick up. The next action is the human merging
the three local branches in priority order.

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
- **lock-single-flight** — `lock.py`: PID-stamped lock file keyed to the
  todo path, live-lock check exits 0 with `already_running`, stale-lock
  reclaim journalled and run proceeds. Wired into `cli.py`'s `gsd run` before
  ingest. Closed: AC10.5, AC10.6.
- **schedule-os-backends** — `schedule.py`: `gsd schedule add/list/remove/show`
  with `crontab` (Linux/BSD) and `launchd` plist (macOS) backends; marker-
  delimited entries; `add` prints before writing; absolute paths and
  `--non-interactive` in installed command; invalid cron expressions rejected;
  timezone recorded and printed. Closed: AC10.1–AC10.4, AC10.7–AC10.9.
- **gsd-doctor** — `doctor.py` + `gsd doctor` CLI command: config validity,
  harness binary on PATH, allow-list sanity, installed schedule entries
  readable, todo file parseable. Actionable error per failure.
  Closed: Phase 6 doctor requirement.
- **ci-and-coverage-floor** — GitHub Actions workflow running `make check` on
  push; per-module coverage floor at 90% enforced (per the testing strategy in
  `specs/04`).
  Closed: `specs/04` §Testing strategy coverage floor.

---

## In-flight (local branches — not yet pushed or merged)

These items are built and committed on local branches. The build beat must not
re-implement them. The human must push and merge them in priority order.

### Phase 7 — unattended hardening

**failure-backoff-and-park** *(branch: `failure-backoff-and-park`, 1 commit
ahead of main)* — Cross-run failure escalation: `@failed=N` token advance on
terminal failure, `@paused=auto-failures` appended at `max_consecutive_failures`
threshold (new config key, default 3), `select.py` treats `@paused=` items as
ineligible (Parked category), byte-preserving todo-file writes, `--dry-run`
leaves file unchanged, successful completion removes `@failed=`. A failed
recurring item's schedule stays untouched (AC8.7 unchanged).
*Validation:* `make check` + `pytest tests/test_backoff.py -q`
*Closes:* new behaviour (proposed AC2.11, AC8.8 — USER-side spec amendment).

### Phase 8 — operator visibility

**gsd-status** *(branch: `gsd-status`, 1 commit ahead of main)* — `status.py`
+ `gsd status` CLI command: item-centric view across the todo file and all run
journals. Per-item line: state, last attempt outcome, consecutive-failure count,
next eligibility instant for deferred items. Derived via `journal.py` replay;
corrupt journals degrade gracefully; read-only (no run directory created); exit
0 always.
*Validation:* `make check` + `pytest tests/test_status.py -q`
*Closes:* new behaviour (proposed AC11.x — USER-side spec amendment).

**json-output** *(branch: `json-output`, 1 commit ahead of main)* — `--json`
flag on `gsd plan`, `gsd status`, and `gsd report`: same facts as human
rendering as a single JSON document on stdout; warnings to stderr; schema from
existing dataclasses in `models.py` / `report.py`; ISO-8601 UTC timestamps;
exit-status contract unchanged.
*Validation:* `make check` + `pytest tests/test_json_output.py -q`
*Closes:* new behaviour (USER-side spec amendment alongside status contract).

---

## Work items (priority order)

NOTE: the "In-flight" section above is stale — `failure-backoff-and-park`,
`gsd-status` and `json-output` are all merged to main as of 2026-08-08.

### Phase 6b — enforce the security controls that are only documented

**Highest priority.** Re-added by hand for the third time: a `plan` beat
regenerates this file from `specs/`, and these findings came from a code audit
rather than from a spec, so every regeneration drops them. **The durable fix is
to add them to `specs/02-functional-spec.md` as acceptance criteria** — a
USER-side edit, since build iterations may not touch specs. Until that happens,
expect to re-add this section after each plan beat.

S1. **enforce-command-allowlist** — `config.is_allowed()` is defined,
   documented in `specs/`, `README.md`, `USAGE.md` and `AGENTS.md`, and
   **called from nowhere**; `doctor.py` reads the lists only to report them.
   Call it in `verify/command.py` before `subprocess.run`, refusing a
   disallowed argv as **`inconclusive`** (never `failed`, never `passed`) with
   `capability_denied` in the evidence. A `command` check's argv is
   model-chosen, so today the verifier executes whatever the decomposition
   proposes. `VerifyContext` does not carry `config`; thread it.
   *Validation:* `make check` + `pytest tests/test_verify_command.py -q` — a
   denied argv yields `inconclusive` and **spawns no process** (assert with a
   runner spy); an allowed argv still runs; the refusal reaches the journal.

S2. **enforce-the-send-boundary** — `network` is granted by default (decided
   2026-08-08), so the enforced boundary is *send*, not *fetch*. Deny `mail`,
   `mailx`, `sendmail`, `ssh`, `scp`, `sftp` wherever gsd dispatches a process,
   and re-express the `git push` / `gh` denial so `git -C … push` and
   `bash -c 'git push'` do not defeat it. Harnesses that cannot express tool
   restrictions (`codex`, `cursor`, `gemini`, `kiro`) must be gated behind an
   explicit `allow_unrestricted_harness = true` or dropped from
   `SUPPORTED_HARNESSES`. Keep shipping the caveat with the code: with
   `python3` allowed and the network reachable this cannot stop a determined
   agent; the container is the control.
   *Validation:* `make check` + `pytest tests/test_harness_argv.py -q` —
   argv-level only, no agent launched.

S3. **capability-honesty** — DECIDED: `Capability` is a declaration, not a
   sandbox, and `specs/03-data-model.md` says so. Remaining sweep: make every
   other statement agree, and add a test asserting the gate's actual contract
   (declared-subset check) rather than the containment previously implied.

### Phase 8b — make a run observable

P1. **run-progress-output** — `gsd run` currently prints **nothing** between
   the gate and the final summary: `run_item`, `execute.py`, `repair.py` and
   every verifier are silent, and the agent subprocess runs with
   `capture_output=True` so its output never reaches the terminal. With a
   15-subtask item and a 900 s per-subtask timeout that is potentially hours of
   blank terminal with no way to distinguish working from wedged. Emit one line
   per stage transition to **stderr** (so stdout stays clean for `--json`):
   subtask index and count, description, then the verdict — e.g.
   `[3/15] Write marker.txt … verifying … passed`. Include repair attempts
   (`repairing (1/2)`) and the final per-item outcome. Add `--verbose` to
   stream the agent's captured stdout as it arrives. Suppress all of it under
   `--json` and when stderr is not a TTY, so scheduled runs stay quiet.
   *Validation:* `make check` + `pytest tests/test_run_progress.py -q` — a
   progress line per subtask on stderr and **none on stdout**; nothing emitted
   under `--json`; `--verbose` includes agent output; a scheduled
   (`--non-interactive`, non-TTY) run emits no progress chatter.
   *Closes:* no existing AC — `specs/02` §Stage 5 says nothing about progress
   output, which is exactly why this was never built. Proposed AC5.6 is a
   USER-side spec amendment.
   *Branch slug:* `run-progress-output`

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
- Do not install a recurring schedule against a real todo list until
  `failure-backoff-and-park` has landed — until then a failing item is
  re-attempted (and its agent budget re-spent) on every firing.
- When `failure-backoff-and-park` lands, add the proposed AC2.11 (select
  excludes `@paused=` items, reported as Parked) and AC8.8 (failed terminal
  state advances `@failed=`; threshold appends `@paused=auto-failures`) to
  `specs/02-functional-spec.md` — the loop may not edit specs itself.
- When `gsd-status` / `json-output` land, add the corresponding contract
  sections (proposed AC11.x) to `specs/02-functional-spec.md` — same rule.
