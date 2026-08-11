<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-11

All planned phases through Phase 13 N1 and Phase 12 F1 are merged to main.
No local work-item branches remain.

- C1 (`thread-check-rejection-reason`), R1 (`resume-can-retry-a-failed-item`),
  D1 (`validate-depends-targets-at-ingest`), N1 (`choose-the-name`), and
  F1 (`friendly-run-ids`) all landed on 2026-08-10 and 2026-08-11.
- N1 decision: **jumar** (PyPI 404 confirmed 2026-08-10; human must confirm
  `jumar.dev` availability at a registrar before N2 begins).
- F1 shipped items 1–3 (prefix matching, `latest`, new id format). Item 4
  (`runs/index.tsv` run index) was deferred — see F2 below.
- **Sequencing note (stale):** the prior plan said to do N2 before F1. F1 has
  shipped under the old name. N2 still must happen before any publication —
  the cost is the same, just no longer affects the id format.
- **USER-side spec amendments applied 2026-08-11:** AC2.11, AC2.12,
  AC3.7–3.8, AC5.6, AC6.8–6.9, AC8.8, Stage 11 (AC11.1–11.6) and AC-S5 are
  now in `specs/02-functional-spec.md`; the run-id format/resolution rules,
  backoff tokens, journal events and config reference are in
  `specs/03-data-model.md`; the send-boundary security posture is reconciled
  in `specs/04-technical-plan.md` and `USAGE.md` (README was already
  correct); `[gsd.harness.<stage>]` and the model-selection guidance
  (decompose high, execute cheap, judge independent) are in `USAGE.md` §7.
  They are no longer "proposed" — the `plan` beat should stop re-deriving
  them as gaps.

### Completed (merged to main)

- **models-and-invariants** — data model shapes, Check invariants at construction (AC3.1 model, AC3.4).
- **config-and-capabilities** — `config.py`: gsd.toml loading, capability set, is_allowed() (config contract).
- **journal-append-and-replay** — `journal.py`: append-only journal, strictly-increasing seq, replay, corrupt-line tolerance (AC-S1–S4).
- **ingest-markdown-todos** — `ingest.py`: GFM task list, nested subtasks, @metadata, schedule tokens, stable item_id (AC1.1–1.10).
- **select-next-item** — `select.py`: eligibility, dependency gate, cycle detection, sort (AC2.1–2.10).
- **cli-plan-dry-run** — `cli.py`: gsd plan --dry-run, clock.py injectable now, run_started journalled (CLI read-only path).
- **harness-argv** — `harness.py`: agent-CLI abstraction, scrubbed env, hard deny of git push/gh (AC8.4; specs/04 §Harness).
- **decompose-with-required-checks** — `decompose.py`: structured plan, one retry, hard rejections, full plan journalled (AC3.1–3.6).
- **gate-modes** — `gate.py`: --dry-run/--approve/--auto dispatch, capability refusal, startup error (AC4.1–4.4).
- **execute-and-verify-command** — `execute.py` + `verify/command.py`: subtask dispatch, wall-clock timeout, evidence, command verifier (AC5.1–5.5, AC6.1, AC6.3, AC6.6, AC6.7).
- **verify-file-and-absence** — `verify/file.py`: file (exists + pattern + hash) and absence (path/glob gone) verifiers (AC6.2).
- **repair-bounded** — `repair.py`: bounded repair retries, evidence injection, terminal state on budget exhaustion, --halt-on-fail (AC7.1–7.4).
- **complete-item** — `complete.py`: byte-preserving checkbox flip, optional branch commit, no push/PR (AC8.1–8.4).
- **report-and-resume** — `report.py` + gsd resume: per-run report, exit status, partial/deferred/overdue handling, journal replay + continue (AC9.1–9.5, AC-S1, AC-S4).
- **verify-judge-adversarial** — `verify/judge.py`: fresh context, default-fail, structured verdict, inconclusive on unparseable (AC6.4).
- **verify-manual** — `verify/manual.py`: interactive confirm, inconclusive under --non-interactive (AC6.5).
- **clock-and-recurrence** — `recurrence.py`: next-occurrence arithmetic, DST resolution; complete.py advances @not-before= in place (AC8.5–8.7).
- **lock-single-flight** — `lock.py`: PID-stamped lock keyed to todo path, live/stale detection; wired into gsd run (AC10.5, AC10.6).
- **schedule-os-backends** — `schedule.py`: gsd schedule add/list/remove/show with crontab + launchd backends (AC10.1–10.4, AC10.7–10.9).
- **gsd-doctor** — `doctor.py` + gsd doctor: config validity, harness PATH, allow-list sanity, schedule readability, todo parseability (Phase 6 doctor).
- **ci-and-coverage-floor** — GitHub Actions on push, 90% per-module coverage floor (specs/04 §Testing).
- **failure-backoff-and-park** — `backoff.py`: @failed=N advance, @paused=auto-failures at threshold, Parked select category (AC2.11, AC8.8).
- **gsd-status** — `status.py` + gsd status: item-centric view across todo and journals (AC11.1–11.4).
- **json-output** — --json flag on gsd plan/run/report (not status yet), ISO-8601 UTC timestamps, schema from existing dataclasses (AC11.5–11.6).
- **run-progress-output** — `progress.py`: stderr stage lines per subtask, --verbose agent stdout, suppressed under --json/non-TTY (AC5.6).
- **reject-shell-wrapper-checks** — `Check.__post_init__` refuses `kind=command` with a shell (`bash`, `sh`, `zsh`, etc.) plus inline-program flag (`-c`, `--command`); a wrapper running a file stays legal. Tests in `tests/test_hollow_checks.py` (AC3.7).
- **checks-must-be-falsifiable** — `Check.__post_init__` refuses `kind=command` whose argv[0] cannot fail for any reason connected to the work (`true`, `echo`, `date`, `ls` with no operands, etc.). Tests in `tests/test_hollow_checks.py` (AC3.8).
- **fetch-failure-must-fail-the-subtask** — `verify/file.py` returns `failed` for a zero-byte path with `file_is_empty` evidence before any pattern matching; `content_head` included in evidence on pattern miss. Tests in `tests/test_hollow_checks.py` (AC6.8).
- **enforce-command-allowlist** — `verify/command.py` calls `is_allowed()` before `subprocess.run`; denied argv yields `inconclusive` with `capability_denied` and spawns no process; `VerifyContext` carries `config`; default policy applies when config absent. Tests in `tests/test_hollow_checks.py` (AC6.9 = S1).
- **enforce-the-send-boundary** — deny send vectors (`mail`, `mailx`, `sendmail`, `ssh`, `scp`, `sftp`, `rsync`) everywhere gsd dispatches a process; fix `git -C … push` bypass; gate unrestricted harnesses behind `allow_unrestricted_harness = true`. (S2)
- **capability-honesty** — `Capability` is a declared-subset check, not a runtime sandbox; sweep all remaining statements into agreement and add a test asserting the gate's actual contract. (S3)
- **per-stage-model-selection** — `HarnessConfig.for_stage()` with `[harness.<stage>]` overrides in gsd.toml; resolved model journalled in every `HarnessInfo`. (M1)
- **wire-the-judge-harness** — pass `harness=` and `judge_timeout_s` at both `VerifyContext` construction sites (`run_item` and `repair.py`) so `kind=judge` checks actually reach the judge verifier in a real run. (M2)
- **thread-check-rejection-reason** — C1. Propagate `ValueError` message from `Check.__post_init__` into the decompose retry prompt so the model knows which rule it violated (AC3.7 follow-up).
- **resume-can-retry-a-failed-item** — R1. `gsd resume <run-id> --retry-failed` re-enters `run_item` from the first unverified subtask; report takes latest terminal event per item; advance_failure_count not double-counted (AC-S5).
- **validate-depends-targets-at-ingest** — D1. Every `@depends=` target must resolve to an item in the same file; unresolvable target is a startup error with did-you-mean suggestion (AC2.12).
- **choose-the-name** — N1. Collision checks run against PyPI JSON API, GitHub, and domains for 30+ candidates. Decision: **jumar** (PyPI 404 confirmed 2026-08-10). Human must verify `jumar.dev` at a registrar before N2 begins.
- **friendly-run-ids** — F1. Items 1–3: new id format `YYYYMMDD-HHMM-<4 hex>` via `clock.make_run_id()`; prefix matching; `latest` resolution. Item 4 (runs/index.tsv) deferred to F2.

### In-flight (local branches — not yet pushed)

None.

---

## Work items (priority order)

### Phase 13 — rename the project

N2. **rename-everything** — One branch, one commit, mechanical. Do it **before
   anything is published**; the cost roughly doubles once a package name exists
   on an index and a schedule exists on someone's machine.

   **Precondition:** human must confirm `jumar.dev` is available at a registrar
   and that `github.com/jumar` (personal keyboard hobbyist account) does not
   create a branding conflict. If jumar is disqualified, fall back to `proofstep`
   (check `proofstep.dev` similarly). Only begin this item after that confirmation.

   **NOT a build iteration.** This item touches `specs/` — the plan beat and
   update beat may work on it, and the human may merge both halves in one commit;
   the build beat must skip it. The full rename belongs in a single commit so the
   grep gate passes without an intermediate broken state.

   Measured surface as at 2026-08-09 — `gsd` / `getstuffdone` / `GetStuffDone`
   respectively: `src/` 145/54/37, `tests/` 145/239/29, `specs/` 28/15/9,
   `tools/` 0/6/13. Plus `README.md`, `USAGE.md`, `AGENTS.md`, `Makefile`,
   `pyproject.toml`, `.gitignore`, and the four `tools/spec-loop/` prompt files.

   Identifiers that are **not** just prose and must each be decided deliberately:

   - `pyproject.toml`: `name = "getstuffdone"`, the `[project.scripts]` entry
     point, and the `[tool.gsd]` config table name.
   - The package directory `src/getstuffdone/`.
   - The CLI verb itself — `gsd run` becomes `<name> run`.
   - `config.py`: the `gsd.toml` filename and the `[gsd]` section header.
     **This one breaks every existing config file.** Support the old name for a
     release with a deprecation notice, or accept the break and say so in the
     README — but decide, do not discover.
   - `lock.py`: `_LOCK_FILENAME = ".gsd.lock"`. A rename means a run in progress
     under the old name is invisible to the new one.
   - `schedule.py`: `_META_PREFIX = "# gsd-meta: "` and the launchd label
     `com.gsd.<id>.plist`. **This is the load-bearing one.** Installed cron and
     launchd entries are found by those markers. Rename them and every
     already-installed schedule becomes both orphaned and broken.

     Handle it explicitly: `<name> doctor` should detect old-marker entries and
     name them, and the release notes must say "run `gsd schedule remove` for
     each entry **before** upgrading, then reinstall". Do **not** have the new
     binary rewrite entries it did not author — AGENTS.md forbids touching
     crontab lines outside our own markers.

   Deliberately **not** renamed: existing `runs/` directories and journal
   contents. They are an append-only historical record.

   *Validation:* `make check` + a grep gate — `grep -ri 'gsd\|getstuffdone\|GetStuffDone' src tests specs tools *.md *.toml Makefile` returns only deliberate historical references (e.g. mentions in `IMPLEMENTATION_PLAN.md`'s Completed section). Add that grep to CI so the old name cannot creep back in.
   *Closes:* nothing — pure rename. `specs/` are touched so the build beat
   cannot take this item; it requires a human edit or plan+update-beat
   collaboration.
   *Branch slug:* `rename-everything`

### Phase 12 follow-up — run index

F2. **run-index-tsv** — Item 4 of the `friendly-run-ids` work was deferred:
   `runs/index.tsv`, one line per run (`id \t started \t item_id \t status`),
   appended at `run_started` and updated at `run_finished`. Without it, `gsd
   status` works (it scans all journals via `_scan_runs` in `status.py`) but
   gets slower as the runs directory grows, and there is no fast way to answer
   "which run id corresponds to the ASF item?" without opening each journal.

   The index is append-only except for the `status` column update at
   `run_finished`; the update rewrites that line in place using the run_id as a
   key. Backwards-compatible: uuid-named runs already in `runs/` get no index
   entry and are still resumable via prefix matching.

   When the index exists, `_resolve_run_id` in `cli.py` can use it to resolve
   `latest` without reading journals — but journals remain the authoritative
   record and the index is a cache only; a missing or corrupt index falls back
   to the journal scan.

   *Validation:* `make check` + `pytest tests/test_resume.py tests/test_report.py -q`
   — a fresh run appends a line to `runs/index.tsv`; `run_finished` updates the
   status column; a run that crashes mid-way leaves its row with status
   `in_progress`; existing uuid-named runs not in the index still resolve via
   the journal-scan fallback; a corrupt or absent index.tsv falls back to
   journal scan without error.
   *Closes:* deferred from F1 (friendly-run-ids).
   *Branch slug:* `run-index-tsv`

### Small items (unscheduled)

- **runs-dir-relative-path** (from R1) — `resume` resolves `--runs-dir`
  relative to the current working directory (default `runs`), so a bare run id
  only works from the directory the run was launched in. Either record the
  absolute runs directory in the journal too, or say so in the error — the
  current message ("no journal found at runs/<id>/journal.jsonl") does not
  hint that the path is relative.
- **status-json** — `gsd status` has no `--json` yet (the flag exists on
  plan/run/report); noted as a known gap in `specs/02` §Stage 11.

---

## Guardrails (do not re-plan these)

- **Verification is not optional and not deferrable.** No work item may ship a
  stage that performs work ahead of the check that proves it worked.
- **A check may never be weakened to pass.** Not a test, not an AC, not a
  subtask's own `Check` at runtime (that is what AC7.3 exists to enforce).
- **The enforced boundary is *send*, not *fetch*** (decided 2026-08-08).
  `network` IS a default capability and `curl`/`wget` are allowed. `mail`,
  `mailx`, `sendmail`, `ssmtp`, `msmtp`, `ssh`, `scp`, `sftp`, `rsync` are
  denied, and `git push` / `gh` stay hard-denied in every dispatched argv. Do
  not plan a work item that relaxes the send boundary — and do not plan one that
  claims the allow list is a sandbox: with `python3` allowed and the network
  reachable it is defence in depth, and the container is the control.
- **No `shell=True`.** Every subprocess is argv. Do not plan a shell-string
  escape hatch.
- **Out of scope for v1** (do not plan): parallel subtask execution, cross-item
  planning, non-Markdown todo inputs, sync with external trackers (Jira, Asana,
  Todoist), a resident daemon, a web UI, Windows Task Scheduler, and
  **cross-file `@depends=`** (decided with D1: one file's eligibility must not
  depend on another file's run history — two related lists state their run
  order in prose).
- **The product pointing at its own plan** is a milestone, not a dependency.
  Nothing in `src/getstuffdone/` may assume it is being run against this repo.

## Manual follow-ups (USER-side; not loop work items — do not build these)

- **Confirm `jumar.dev` availability** at a domain registrar before N2 begins.
  If taken, fall back to `proofstep` (check `proofstep.dev` too). Also confirm
  that `github.com/jumar` (personal account, keyboard hobbyist) does not block
  creating a `github.com/jumar-dev` or `github.com/jumar-run` org.
- Decide whether `todo.md` and `runs/` should be git-ignored in your working
  copy (the shipped `.gitignore` ignores `runs/`, `gsd.toml` and `todo.md` by
  default — remove those lines if you want the list tracked).
- Confirm which agent CLI is on PATH before the first loop run
  (`SPEC_LOOP_AGENT`, default `claude`).
- Run the loop inside a sandbox with no push credentials in the environment.
- Regenerate the `USAGE.md` transcripts against the current build — §2 and §4
  still show uuid-era run ids, and the doc promises real output.

Done 2026-08-11 (every "when X lands" spec/doc amendment — X has landed; see
the Status note at the top for the full list): the proposed ACs are in
`specs/02-functional-spec.md`, the run-id/backoff/config updates in
`specs/03-data-model.md`, and the send-boundary posture, `[gsd.harness.<stage>]`
reference and model-selection guidance in `specs/04-technical-plan.md` /
`USAGE.md`.
