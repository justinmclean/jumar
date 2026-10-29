<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — Jumar (formerly GetStuffDone)

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## What's been built

**Current state (2026-10-29):** `main` HEAD is `2318b06` (PR #43). Every
planned phase is merged, including the three decision-gated items the previous
plan carried (W5, W10, W11). Spec sync branch `sync-specs-20261029-121419` (not yet merged)
documents `jumar-gh`, absence checks with `pattern`, the exit-0 harness
error rule and the `@max-subtasks=` warning.

### Completed items (merged to main unless noted)

- **models-and-invariants** — data model shapes, `Check` invariants at construction (AC3.1, AC3.4).
- **config-and-capabilities** — `config.py`: `jumar.toml` loading, capability set, `is_allowed()`.
- **journal-append-and-replay** — `journal.py`: append-only journal, strictly-increasing seq, replay, corrupt-line tolerance (AC-S1–S4).
- **ingest-markdown-todos** — `ingest.py`: GFM task list, nested subtasks, @metadata, schedule tokens, stable item_id (AC1.1–1.10).
- **select-next-item** — `select.py`: eligibility, dependency gate, cycle detection, sort (AC2.1–2.10).
- **cli-plan-dry-run** — `cli.py`: `jumar plan --dry-run`, injectable `clock.py`, `run_started` journalled.
- **harness-argv** — `harness.py`: agent-CLI abstraction, scrubbed env, hard deny of git push/gh (AC8.4).
- **decompose-with-required-checks** — `decompose.py`: structured plan, one retry, hard rejections, full plan journalled (AC3.1–3.6).
- **gate-modes** — `gate.py`: --dry-run/--approve/--auto dispatch, capability refusal, startup error (AC4.1–4.4).
- **execute-and-verify-command** — `execute.py` + `verify/command.py`: subtask dispatch, wall-clock timeout, evidence, command verifier (AC5.1–5.5, AC6.1, AC6.3, AC6.6, AC6.7).
- **verify-file-and-absence** — `verify/file.py`: file (exists + pattern + hash) and absence (path/glob gone) verifiers (AC6.2).
- **repair-bounded** — `repair.py`: bounded repair retries, evidence injection, terminal state on budget exhaustion, --halt-on-fail config plumbing (AC7.1–7.4).
- **complete-item** — `complete.py`: byte-preserving checkbox flip, optional branch commit, no push/PR (AC8.1–8.4).
- **report-and-resume** — `report.py` + `jumar resume`: per-run report, exit status, partial/deferred/overdue handling, journal replay + continue (AC9.1–9.5, AC-S1, AC-S4).
- **verify-judge-adversarial** — `verify/judge.py`: fresh context, default-fail, structured verdict, inconclusive on unparseable (AC6.4).
- **verify-manual** — `verify/manual.py`: interactive confirm, inconclusive under --non-interactive (AC6.5).
- **clock-and-recurrence** — `recurrence.py`: next-occurrence arithmetic, DST resolution; `complete.py` advances `@not-before=` in place (AC8.5–8.7).
- **lock-single-flight** — `lock.py`: PID-stamped lock keyed to todo path, live/stale detection; wired into `jumar run` (AC10.5, AC10.6).
- **schedule-os-backends** — `schedule.py`: `jumar schedule add/list/remove/show` with crontab + launchd backends (AC10.1–10.4, AC10.7–10.9).
- **jumar-doctor** — `doctor.py` + `jumar doctor`: config validity, harness PATH, allow-list sanity, schedule readability, todo parseability.
- **ci-and-coverage-floor** — GitHub Actions on push, 90% per-module coverage floor.
- **failure-backoff-and-park** — `backoff.py`: `@failed=N` advance, `@paused=` at auto-failure threshold, Parked select category (AC2.11, AC8.8).
- **jumar-status** — `status.py` + `jumar status`: item-centric view across todo and journals (AC11.1–11.4).
- **json-output** — `--json` on plan/run/report, ISO-8601 UTC timestamps, schema from existing dataclasses (AC11.5–11.6).
- **run-progress-output** — `progress.py`: stderr stage lines per subtask, --verbose agent stdout, suppressed under --json/non-TTY (AC5.6).
- **reject-shell-wrapper-checks** — `Check.__post_init__` refuses `kind=command` with a shell (`bash`, `sh`, `zsh`, ...) plus inline-program flag; a wrapper running a file stays legal (AC3.7).
- **checks-must-be-falsifiable** — `Check.__post_init__` refuses `kind=command` whose argv[0] cannot fail for any reason connected to the work (AC3.8).
- **fetch-failure-must-fail-the-subtask** — `verify/file.py` fails a zero-byte path with `file_is_empty` evidence before pattern matching; `content_head` included on pattern miss (AC6.8).
- **enforce-command-allowlist** — `verify/command.py` calls `is_allowed()` before `subprocess.run`; denied argv is `inconclusive`/`capability_denied`, no process spawned (AC6.9/S1).
- **enforce-the-send-boundary** — deny send vectors (`mail`, `mailx`, `sendmail`, `ssh`, `scp`, `sftp`, `rsync`) everywhere jumar dispatches a process; closed the `git -C … push` bypass; unrestricted harnesses gated behind `allow_unrestricted_harness` (S2).
- **capability-honesty** — `Capability` documented and tested as a declared-subset check, not a runtime sandbox (S3).
- **per-stage-model-selection** — `HarnessConfig.for_stage()` with `[harness.<stage>]` overrides; resolved model journalled in every `HarnessInfo` (M1).
- **wire-the-judge-harness** — `harness=`/`judge_timeout_s` threaded to both `VerifyContext` construction sites so `kind=judge` checks reach the judge verifier in a real run (M2).
- **thread-check-rejection-reason** — C1. `ValueError` message from `Check.__post_init__` propagated into the decompose retry prompt (AC3.7 follow-up).
- **resume-can-retry-a-failed-item** — R1. `jumar resume <run-id> --retry-failed` re-enters `run_item` from the first unverified subtask (AC-S5).
- **validate-depends-targets-at-ingest** — D1. Unresolvable `@depends=` target is a startup error with did-you-mean suggestion (AC2.12).
- **choose-the-name** — N1. Decision: **jumar** (PyPI 404 confirmed 2026-08-10).
- **friendly-run-ids** — F1 items 1–3. `YYYYMMDD-HHMM-<4 hex>` ids via `clock.make_run_id()`, prefix matching, `latest` resolution.
- **block-mcp-server-inheritance** — `--strict-mcp-config` in the Claude harness argv so user MCP servers can't bypass the send-boundary deny list.
- **runs-dir-relative-path** — `_resolve_run_id` + `_RunResolveError` in `cli.py`; prefix matching and `latest` wired into `resume`/`report`; CWD-aware errors.
- **status-json** — `--json` on `jumar status` (AC11.5, AC11.6).
- **run-index-tsv** — F2. `runs/index.tsv` appended at `run_started`, status updated at `run_finished`; `_resolve_run_id` uses it for `latest`, falls back to journal scan.
- **implementation-guide-run-context** — `USAGE.md` §4 "What happens during a run".
- **reuse-the-execution-session** — P3. One execution session per item (`--session-id`/`--resume`) minted at `plan_created`; repair continues it; verifiers/judge always get a fresh context.
- **rename-everything** — N2. Full rename GetStuffDone/`gsd` → **jumar**: package, entry point, config, lock file, schedule markers, docs, specs; `doctor` detects pre-rename schedule entries; CI grep gate blocks the old name.
- **local-model-harness** — L1 (Phase 15). `openai_agent.py`: in-process `agent = "openai"` harness against any OpenAI-compatible endpoint, gated tool loop, no `allow_unrestricted_harness` needed; `doctor.py` probes `/models`. Merged PR #10.
- **harness-outage-budget-exclusion** — `harness.detect_harness_error()` + `backoff.advance_failure_count()`: a harness-level outage no longer counts against an item's `@failed=` budget. Merged PR #8.
- **session-uuid-pinning** — `clock.make_session_id()` mints a real UUID4; fixed a bug where every multi-subtask item's second subtask failed outright. Merged PR #7.
- **ingest-warns-on-unparseable-lines** — W1 (Phase 16). A line matching none of the task-list/context/heading patterns is classified `malformed` with a parse warning naming the line number (AC1.6). Merged PR #12.
- **journal-item-deferred-event** — W2 (Phase 16). `journal.ITEM_DEFERRED` journalled once per deferred/parked item when `select_next()` returns no selection. Merged PR #13.
- **close-thin-ac-test-coverage** — W4 (Phase 16). Added missing literal assertions for AC5.2, AC5.6, AC6.8, AC10.6, AC10.8, AC-S5. Merged PR #14.
- **systemd-schedule-backend** — W3 (Phase 16). `SystemdBackend` alongside `CronBackend`/`LaunchdBackend`; `default_backend()` prefers it on Linux when `systemctl --user` is reachable; `doctor.py` covers it via the existing backend-agnostic `list_schedules()`; full `TestSystemdBackend`/`TestSystemdActivation`/`TestDefaultBackendSystemd` suite. Merged PR #17.
- **schedule-log-redirection-and-id-validation** — W6 (Phase 17). `ScheduleEntry.log_path`, backend stdout/stderr redirection, log directory creation, and `[a-z0-9-]{1,32}` schedule id validation. Merged PR #17.
- **test-due-field-inertness** — W7 (Phase 17). Static regression coverage that `due` stays inert outside selection/reporting/status code. Merged PR #17.
- **scheduled-run-config-resolution** — W8 (Phase 17). `--config` support for scheduled `run` invocations, explicit config-path loading, and backend working-directory pinning. Merged PR #17.
- **decompose-failure-mode-distinction** — W9 (Phase 17). Distinct timeout/empty/unparseable decompose rejection reasons and empty OpenAI-compatible harness response reporting. Merged PR #17.
- **doctor-config-file-presence** — W12 (Phase 17). `doctor` reports config source and warns when using built-in defaults because no config file was found. Merged PR #17.
- **versioned-python-command-policy** — follow-up from PR #17. `python3.N` executables such as `python3.12` match the default `python3` command policy while still respecting `python3` deny rules.
- **resolve-halt-on-fail-semantics**: W5. `jumar run --until-empty` loops over eligible items; `--halt-on-fail` / `halt_on_fail` stops it after the first failure (AC7.4). Merged PR #35.
- **authored-check-pinning**: W10. A `check: <argv>` line under an authored subtask becomes that subtask's check; refusals are startup errors and `@check=` is a parse warning (AC1.11). Merged PR #34.
- **plan-dry-run-decompose-contract**: W11. Resolved in the docs: the README points at `jumar run --dry-run` for decompose-and-print; `plan --dry-run` stays ingest, select and print. Merged PR #36.
- **honour-max-subtasks-override**: `@max-subtasks=` reaches the decompose prompt and length check via `decompose.item_max_subtasks()`; a non-positive value is a parse warning. Merged PR #37.
- **regenerate-usage-transcripts**: `USAGE.md` §2/§4 transcripts regenerated with friendly run ids. Merged PR #39.
- **clean-exit-not-harness-error**: `detect_harness_error()` never classifies an attempt that exited 0. Merged PR #41.
- **absence-pattern**: `kind=absence` with `pattern` asserts no named file contains the regex. Merged PR #42.
- **jumar-gh-readonly**: `gh_readonly.py` + `jumar-gh` console script: read-only GitHub through a parsed allow list, `gh` itself still hard-denied. Merged PR #43.

---

## In flight (do not build)

- **reject-non-portable-grep-checks**: open PR #44 (`fix/reject-non-portable-grep-checks`).
- **human-pause and recurring dependents**: uncommitted in the main checkout,
  sitting on `feat/jumar-gh-readonly`, which PR #43 already merged. Adds
  `@paused=human` (never selected, reported as "With a person" rather than
  parked) and lets a recurring item that completes in an `--until-empty` pass
  unblock its dependents in the same pass. Needs its own branch off `main`
  before it can be committed. Once merged, an `update` beat must add both to
  `specs/02-functional-spec.md` (Parking, AC2.11, Stage 2 dependency gate) and
  `specs/03-data-model.md`.

## Work items (priority order)

W13. **cite-ac-ids-in-tests**: `tests/` names no test for AC1.11, AC2.11,
   AC3.7, AC3.8, AC6.8, AC6.9, AC8.8, AC11.2, AC11.3 or AC11.4. The behaviour is
   probably covered under other names (W4 closed the last round of this), so
   the work is to find each covering test and cite the AC id in its docstring,
   and to add an asserting test, refusal path included, wherever none exists.
   Tests only, no `src/jumar/` change.
   *Validation:* `make check`, then every AC id in
   `specs/02-functional-spec.md` appears in `tests/`:
   `for ac in $(grep -oE '^- AC[-A-Z0-9.]+' specs/02-functional-spec.md | sed 's/- //' | sort -u); do grep -rqE "\b$ac\b" tests/ || echo "$ac"; done`
   prints nothing.
   *Closes:* the AC ids above.
   *Branch slug:* `cite-ac-ids-in-tests`

W14. **spec-loop-single-flight-lock**: `specs/05-operator-tooling.md` Known
   gaps: an overlapping scheduled `loop.sh` invocation duplicates work. Add a
   lock to `tools/spec-loop/lib.sh` with the product's semantics (PID-stamped,
   live lock exits 0 with `already_running`, stale lock reclaimed).
   *Validation:* `bash -n tools/spec-loop/loop.sh tools/spec-loop/lib.sh` +
   `bash tools/spec-loop/tests/test_runner_fixtures.sh` extended with a live
   lock refusing a second run and a stale lock being reclaimed.
   *Closes:* 05's second Known gap.
   *Branch slug:* `spec-loop-single-flight-lock`

W15. **spec-loop-verify-mode**: `specs/05-operator-tooling.md` Known gaps:
   the runner trusts the beat to have run its Validation command.
   `SPEC_LOOP_VERIFY=1` re-runs `make check` after a build iteration commits
   and reports a red result instead of treating the iteration as done.
   *Validation:* as W14, with fixtures for a green and a red `make check`.
   *Closes:* 05's first Known gap.
   *Branch slug:* `spec-loop-verify-mode`

---

## Closed (decided, not building)

P2. **parallel-independent-subtasks** — **CLOSED 2026-08-22 by the human.**
   Serial execution stands. The reason is not that wall-clock exclusivity is
   part of what jumar promises — it is that independence is *asserted by a
   model*, and the assertion cannot be checked mechanically.

   The measurement stands and should not be re-derived. Across five runs
   (2026-08-11): decompose 256–581s; median subtask execution 258–505s; agent
   process startup measured at **4.1s**, moved 46ms by `--strict-mcp-config`.
   Essentially none of the total is tunable overhead — a subtask is a full
   agentic session, eight to ten model round trips. Execution is serial, so
   wall clock is the sum of every subtask plus every repair: fifteen subtasks
   at ~4 minutes is an hour before repairs. Only concurrency changes that
   ceiling; trimming the plan or picking cheaper per-stage models reduces the
   total underneath it.

   **Why closed.** The case *for* concurrency is sound on its own terms: two
   subtasks with no `depends_on` edge neither consume each other's output nor
   can invalidate each other's check, so running them together and verifying
   each against its own check proves exactly what serial execution proves. It
   fails on its premise. `depends_on` and `write_fs` are authored by the same
   model that authors the checks — the DAG is a claim about independence, not
   a proof of it. The proposed guardrail (a `write_fs` collision between
   concurrent subtasks is a planning-time rejection) only catches collisions
   the model *declared*; the failure that matters is the undeclared write —
   two subtasks that both append to `drafts/inventory.md` because neither said
   so. No planning-time check over model-authored metadata can see it, and the
   failure mode is silent corruption rather than a loud error. Parallel safety
   would therefore rest on model judgement, which is the one thing this
   project does not rest on.

   The payoff on the other side of that trade is also smaller than the
   measurement suggests. Scheduling is first-class (launchd/cron, recurrence,
   resume), and for an unattended overnight run wall clock costs nothing; the
   ceiling only bites in foreground use. `max_parallel=2` does not halve a
   fifteen-subtask plan either — DAG width varies by level and verification
   stays serial regardless.

   **Reopen condition.** Revisit if and only if each subtask executes in a
   filesystem sandbox that *enforces* its declared write paths rather than
   trusting them. At that point `depends_on` stops being a claim, the argument
   above becomes sound, and the shape already worked out applies: concurrency
   only within a dependency level, bounded `max_parallel` defaulting to 1,
   verification serial and ordered so the journal stays a single linear record,
   `--approve` pinned to 1. Do not reopen it on wall-clock pain alone.

   *Specs:* no change needed. `specs/04-technical-plan.md` §Deferred already
   lists parallel subtasks; closing P2 leaves that line correct.

---

## Guardrails (do not re-plan these)

- **Verification is not optional and not deferrable.** No work item may ship a
  stage that performs work ahead of the check that proves it worked.
- **A check may never be weakened to pass.** Not a test, not an AC, not a
  subtask's own `Check` at runtime (that is what AC7.3 exists to enforce).
- **The enforced boundary is *send*, not *fetch*** (decided 2026-08-08).
  `network` IS a default capability and `curl`/`wget` are allowed. `mail`,
  `mailx`, `sendmail`, `ssmtp`, `msmtp`, `ssh`, `scp`, `sftp`, `rsync` are
  denied, and `git push` / `gh` stay hard-denied in every dispatched argv
  (read-only GitHub goes through `jumar-gh`, never a relaxed `gh`). Do
  not plan a work item that relaxes the send boundary, nor one that claims the
  allow list is a sandbox: with `python3` allowed and the network reachable
  it is defence in depth, and the container is the control.
- **No `shell=True`.** Every subprocess is argv — do not plan a shell-string
  escape hatch.
- **Out of scope for v1** (do not plan): cross-item planning, non-Markdown
  todo inputs, sync with external trackers (Jira, Asana, Todoist), a resident
  daemon, a web UI, Windows Task Scheduler, and **cross-file `@depends=`**
  (decided with D1: one file's eligibility must not depend on another file's
  run history — two related lists state their run order in prose).
- **Parallel subtask execution is CLOSED, not open** (see P2 above,
  2026-08-22) — serial execution stands because `depends_on`/`write_fs` are
  model-authored claims, not enforced facts, and an undeclared write collision
  is invisible to any planning-time check. Do not re-plan it; the reopen
  condition is written up in P2 and requires an enforced per-subtask
  filesystem sandbox, not just wall-clock pain.
- **The product pointing at its own plan** is a milestone, not a dependency —
  nothing in `src/jumar/` may assume it is being run against this repo.

## Manual follow-ups (USER-side; not loop work items — do not build these)

- **Register `jumar.dev`** (or chosen domain) if wanted — nice-to-have, not a gate.
- Confirm which agent CLI is on PATH before the first loop run
  (`SPEC_LOOP_AGENT`, default `claude`); run in a sandbox with no push credentials.
- **README: quote the wall-clock price** (from P2's closure) — a subtask is a
  full agentic session of eight to ten model round trips, serial because each
  is verified before the next starts, so fifteen subtasks is about an hour
  before repairs; note what reduces it (narrower plan, cheaper per-stage
  models) and that unattended scheduling makes the number moot.
