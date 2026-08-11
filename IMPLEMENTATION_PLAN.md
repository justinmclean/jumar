<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-11

**BLOCKER (recorded by build beat 2026-08-11):** `main` has pre-existing test
failures — `test_resume.py` imports `_resolve_run_id` / `_RunResolveError` from
`cli.py` (added by the F1 commit, ef7a90b), but the implementation lives only on
the `runs-dir-relative-path` local branch, which is not yet merged. Additional
failures exist in `test_decompose.py` (14 tests) and `test_report.py` (3 tests),
also fixed by that branch. `make check` fails on every branch cut from main until
`runs-dir-relative-path` is merged. **The three local branches below must be
merged (in any order) before the next build iteration can commit.**

Two further pieces of ready-to-commit work exist as uncommitted changes on main:
1. `src/getstuffdone/harness.py` + `tests/test_harness_argv.py` — adds
   `--strict-mcp-config` to the Claude harness argv so user-configured MCP
   servers (e.g. a mail server) cannot bypass the send-boundary deny list.
   Branch `block-mcp-server-inheritance` was created for this but cannot be
   committed until the blocker above is resolved.
2. `IMPLEMENTATION_PLAN.md` — the plan updates below (Phase 14 items, corrected
   in-flight list). These were uncommitted changes on main; they are included
   here verbatim.

All planned phases through Phase 13 N1 and Phase 12 F1 are merged to main.

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

- **runs-dir-relative-path** — `_resolve_run_id` + `_RunResolveError` in `cli.py`;
  prefix matching and `latest` wired into `gsd resume` and `gsd report`; CWD-aware
  error messages; C1 follow-up (rejection detail in decompose retry). **Must merge
  first — it fixes the broken test suite on main.**
- **run-index-tsv** — `runs/index.tsv` appended at `run_started`, status column
  updated at `run_finished`; `_resolve_run_id` uses the index for `latest` when
  present, falls back to journal scan (F2).
- **status-json** — `--json` flag on `gsd status` (AC11.5, AC11.6); run-id
  symbol correction from F1.

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
- **implementation-guide-run-context** — The implementation guide needs a
  clearer "what is going on during a run" view than just "read the journal".
  Document how to interpret the live progress output, report, status view, agent
  claims, verifier evidence, repair attempts, and next action together. If that
  write-up exposes a product gap, split the behaviour change into its own work
  item instead of smuggling it into docs.
- **status-json** — `gsd status` has no `--json` yet (the flag exists on
  plan/run/report); noted as a known gap in `specs/02` §Stage 11.

### Phase 14 — the wall-clock ceiling

P2. **parallel-independent-subtasks** — **Measured 2026-08-11**, not assumed.
   Across five runs, decompose takes 256–581s and the median subtask execution
   is 258–505s. Agent process startup was measured directly at **4.1s**, and
   `--strict-mcp-config` changed it by 46ms — so essentially none of it is
   overhead that can be tuned away. A subtask is a full agentic session: read
   prompt, tool call, read result, tool call, write, summarise — eight to ten
   model round trips. Subtask 0 of the tou-osd item ("fetch two documents into
   `sources/`") took **132s**, of which ~4s was startup.

   Execution is strictly serial, so wall clock is the *sum* of every subtask
   plus every repair. Fifteen subtasks at ~4 minutes is an hour before repairs.
   Trimming the plan or picking cheaper models reduces the total; only
   concurrency changes the ceiling.

   **The design question, which must be answered before any code.** "One
   subtask at a time, verified before the next starts" is the product's central
   claim. Does it mean *verification order* or *wall-clock exclusivity*? The
   argument that it means order: two subtasks with no `depends_on` edge between
   them, by construction, neither consumes the other's output nor can invalidate
   the other's check — that is exactly what the DAG asserts, and `decompose`
   already rejects a cyclic one. Running them concurrently and verifying each
   against its own check changes nothing about what is proven.

   The argument for caution, and it is not weak: subtasks that share no declared
   dependency can still collide in the filesystem, and `depends_on` is authored
   by the same model that authors the checks — the graph is a *claim* about
   independence, not a proof of it. Two subtasks that both append to
   `drafts/inventory.md` are independent in the DAG and racing in reality.

   If it goes ahead, the shape that keeps the guarantee:
   - Concurrency only within a dependency level; never across an edge.
   - A bounded worker count (`max_parallel`, default **1** — opt in, never a
     silent behaviour change for existing users).
   - **Verification stays serial and stays ordered**, so the journal remains a
     single linear record and `already_passed` replay is unchanged on resume.
   - A subtask declaring `write_fs` on a path another concurrent subtask
     declares is a planning-time rejection, not a runtime race.
   - `--approve` forces `max_parallel=1`: a human cannot meaningfully approve
     two things at once.

   *Validation:* `make check` + `pytest tests/test_run.py -q` — with
   `max_parallel=1` the observable behaviour is byte-identical to today
   (same journal event order); with `max_parallel=2` two independent subtasks
   overlap in wall clock and their verifications still appear in index order;
   a dependent pair never overlaps; `--approve` pins it to 1; a resume of a
   partially-complete parallel run replays passed subtasks and re-executes only
   the unverified ones.
   *Closes:* nothing yet — this reverses a stated v1 non-goal and needs a
   decision first. `specs/04-technical-plan.md` §Deferred lists parallel
   subtasks; that line and the guardrail below must move together, and specs
   are a human/`update`-beat edit.
   *Branch slug:* `parallel-independent-subtasks`

   **Do not start this before the decision is recorded.** If the answer is that
   serial execution is part of what the tool promises, close the item and say so
   here — an hour for fifteen verified subtasks is then the honest price, and
   the README should quote it rather than leave users to discover it.


P3. **reuse-the-execution-session** — Every subtask spawns `claude -p` in a
   **brand-new conversation**. Measured 2026-08-11: process startup is 4.1s
   (`--strict-mcp-config` changed it by 46ms, so MCP boot is not a factor), and
   a subtask that fetched two documents took 132s. Startup is ~3% — the rest is
   the model working. But a meaningful slice of that work is the model
   re-orienting: every call re-sends the item text, the context prose block and
   the prior-evidence summary, uncached, and the model rebuilds from nothing.

   The comparison that makes it concrete: the same research in a single desktop
   conversation feels far faster, and part of that is real — one conversation
   means prompt-cached input turn over turn, and by step seven the model already
   knows what it did at step three. gsd throws that away fifteen times.

   **This does not touch the product's thesis.** The fresh-context requirement
   is a property of **verification**, not execution — the verifier must never
   see the executing agent's reasoning, only the world it left behind (AC6.4).
   Nothing requires the *executor* to forget. `claude` supports `--session-id`
   and `--resume`, so execution can run as one continued session per item while
   every verifier still gets a brand-new context.

   Shape:
   - One session id per **item**, minted at `plan_created` and journalled so a
     resume can rejoin or deliberately start fresh.
   - `execute()` passes `--resume <id>` for every subtask after the first.
   - **Verification and the judge are unchanged** — always a new context, never
     the execution session. If that separation is ever blurred the adversarial
     judge becomes self-assessment and the guarantee goes with it.
   - Harnesses that cannot resume a session fall back to today's behaviour, the
     same way `allow_tools` degrades for `UNRESTRICTED_HARNESSES`.
   - A repair continues the same session: it is the same subtask, and the
     failing evidence is more useful in context than re-explained.

   **Honest limits.** It will not halve an hour. The dominant cost is output
   generation and tool round trips, which session reuse does not reduce; it
   removes re-reading and re-orientation, so it helps short subtasks more than
   long ones. And it weakens isolation: a session that goes wrong stays wrong
   for the remaining subtasks, where today each starts clean. If that trade is
   unacceptable, say so here and close the item — but it is a smaller trade than
   P2's, because the DAG is not being trusted for anything.

   *Validation:* `make check` + `pytest tests/test_execute.py tests/test_verify_judge.py -q`
   — subtask 2 onwards carries `--resume` with the session id journalled at
   `plan_created`; the judge verifier's argv never carries a session id; a
   harness without resume support produces today's argv unchanged; a resumed run
   either rejoins the recorded session or starts a new one and journals which.
   *Closes:* new behaviour — USER-side spec amendment to `specs/04` §Harness.
   *Branch slug:* `reuse-the-execution-session`

   Do this **before** P2. It is smaller, it needs no decision about what "one
   subtask at a time" means, and it makes every subtask cheaper whether or not
   they ever run concurrently.

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
- **Out of scope for v1** (do not plan): cross-item
  planning, non-Markdown todo inputs, sync with external trackers (Jira, Asana,
  Todoist), a resident daemon, a web UI, Windows Task Scheduler, and
  **cross-file `@depends=`** (decided with D1: one file's eligibility must not
  depend on another file's run history — two related lists state their run
  order in prose).
- **Parallel subtask execution is no longer an automatic "do not plan"** — it is
  now an open question with a written-up item (P2, Phase 14) and an undecided
  premise. It remains **not to be built** until the design question in that item
  is answered. Do not treat its removal from the list above as approval.
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
