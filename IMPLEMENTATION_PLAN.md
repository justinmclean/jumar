<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-10

All planned phases through Phase 9 are merged to main, along with all four
previously in-flight branches (S2 enforce-the-send-boundary, S3
capability-honesty, M1 per-stage-model-selection, M2 wire-the-judge-harness).
No local work-item branches remain.

**Uncommitted working-tree changes on `main`:** `cli.py`, `report.py`, and
`tests/test_run.py` carry a partial implementation of R1
(`resume-can-retry-a-failed-item`): the `--retry-failed` flag, the retry path
in `_cmd_resume`, last-write-wins in `build_report`, and three tests. The build
beat should pick this up first, commit it on the `resume-can-retry-a-failed-item`
branch, and validate with `make check`.

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
- **failure-backoff-and-park** — `backoff.py`: @failed=N advance, @paused=auto-failures at threshold, Parked select category (proposed AC2.11, AC8.8).
- **gsd-status** — `status.py` + gsd status: item-centric view across todo and journals (proposed AC11.x).
- **json-output** — --json flag on gsd plan/status/report, ISO-8601 UTC timestamps, schema from existing dataclasses.
- **run-progress-output** — `progress.py`: stderr stage lines per subtask, --verbose agent stdout, suppressed under --json/non-TTY (proposed AC5.6).
- **reject-shell-wrapper-checks** — `Check.__post_init__` refuses `kind=command` with a shell (`bash`, `sh`, `zsh`, etc.) plus inline-program flag (`-c`, `--command`); a wrapper running a file stays legal. Tests in `tests/test_hollow_checks.py` (proposed AC3.7).
- **checks-must-be-falsifiable** — `Check.__post_init__` refuses `kind=command` whose argv[0] cannot fail for any reason connected to the work (`true`, `echo`, `date`, `ls` with no operands, etc.). Tests in `tests/test_hollow_checks.py` (proposed AC3.8).
- **fetch-failure-must-fail-the-subtask** — `verify/file.py` returns `failed` for a zero-byte path with `file_is_empty` evidence before any pattern matching; `content_head` included in evidence on pattern miss. Tests in `tests/test_hollow_checks.py` (proposed AC6.8).
- **enforce-command-allowlist** — `verify/command.py` calls `is_allowed()` before `subprocess.run`; denied argv yields `inconclusive` with `capability_denied` and spawns no process; `VerifyContext` carries `config`; default policy applies when config absent. Tests in `tests/test_hollow_checks.py` (proposed AC6.9 = S1).
- **enforce-the-send-boundary** — deny send vectors (`mail`, `mailx`, `sendmail`, `ssh`, `scp`, `sftp`, `rsync`) everywhere gsd dispatches a process; fix `git -C … push` bypass; gate unrestricted harnesses behind `allow_unrestricted_harness = true`. (S2)
- **capability-honesty** — `Capability` is a declared-subset check, not a runtime sandbox; sweep all remaining statements into agreement and add a test asserting the gate's actual contract. (S3)
- **per-stage-model-selection** — `HarnessConfig.for_stage()` with `[harness.<stage>]` overrides in gsd.toml; resolved model journalled in every `HarnessInfo`. (M1)
- **wire-the-judge-harness** — pass `harness=` and `judge_timeout_s` at both `VerifyContext` construction sites (`run_item` and `repair.py`) so `kind=judge` checks actually reach the judge verifier in a real run. (M2)

### In-flight (local branches — not yet pushed)

None.

---

## Work items (priority order)

### Phase 6c follow-up — make check-rejection actionable in the retry

C1. **thread-check-rejection-reason** — `_build_check` in `decompose.py` catches
   `ValueError` from `Check.__post_init__` and returns `None`. The retry prompt
   then says only "missing check" rather than naming the rule that was broken
   (shell wrapper, cannot fail, zero-byte, etc.). A second attempt from a model
   that doesn't know what it did wrong tends to produce the same shape.

   Propagate the `ValueError` message into the retry context: instead of
   returning `None`, return the exception message so the caller can include it
   in the "retry because:" field of the decompose prompt. The fix is entirely
   within `decompose.py` — no change to `models.py` or the Check invariants.

   *Validation:* `make check` + `pytest tests/test_decompose.py -q` — a plan
   containing a shell-wrapper check produces a retry prompt that includes the
   specific rule violation text; the retry prompt for a missing check still
   works (backwards-compatible path). An unknown kind returns the existing
   "unrecognised kind" text or similar, not a bare `None`.
   *Closes:* follow-up from enforce-command-allowlist / Phase 6c.
   *Branch slug:* `thread-check-rejection-reason`

### Phase 11 — a failed run must be retryable

R1. **resume-can-retry-a-failed-item** — **Gap, found the hard way on
   2026-08-09.** `_cmd_resume` short-circuits when the resumed item is in
   `state.items_done | state.items_failed`: it rebuilds the report from the
   journal, prints it, and exits without executing anything. So `resume`
   continues an *interrupted* run but cannot retry a *failed* one.

   When a run fails because of a **bug in gsd** — as it did, twice, on the
   unwired judge harness (Phase 9 M2) — the fix is worthless to that run. The
   only way forward is `gsd run` from scratch, re-executing every subtask that
   already passed. Resume also prints the old report verbatim, so it looks
   exactly like the fix did not work.

   Add an explicit retry path — `gsd resume <run-id> --retry-failed` — that
   re-enters `run_item` from the first subtask without a passing verdict,
   keeping the passed ones replayed from the journal. Do **not** change the
   default: the current behaviour is right for the case it was written for, and
   silently retrying a terminal failure would make `resume` non-idempotent.

   Two things it must get right:
   - The retry appends to the same journal, so `item_failed` is no longer the
     last word on that item. `report.py` must take the **latest** terminal event
     per item, not the first, or the report will contradict the run.
   - `advance_failure_count` already ran when the item failed. A retry must not
     double-count, and a subsequent success must clear `@failed=` as usual.

   **NOTE — pre-existing working-tree code:** `cli.py`, `report.py`, and
   `tests/test_run.py` on `main` already contain a partial implementation of
   this item (unstaged). The build beat should commit these changes on the
   `resume-can-retry-a-failed-item` branch without discarding them; validate
   that `make check` passes and that all three new tests in `test_run.py` pass.
   Verify that `advance_failure_count` is not double-counted on retry and that
   `@failed=` is cleared on subsequent success.

   *Validation:* `make check` + `pytest tests/test_resume.py -q` — a journal
   ending in `item_failed` resumes under `--retry-failed` and re-executes only
   the unverified subtasks; the same journal without the flag still reports and
   exits; passed subtasks never re-invoke the agent; the report reflects the
   retry outcome, not the original failure.
   *Closes:* gap in AC-S1/AC-S4 — proposed AC-S5. USER-side spec amendment.
   *Branch slug:* `resume-can-retry-a-failed-item`

   *Related, smaller:* `resume` resolves `--runs-dir` relative to the current
   working directory (default `runs`), so a bare run id only works from the
   directory the run was launched in. Either record the absolute runs directory
   in the journal too, or say so in the error — the current message ("no journal
   found at runs/<id>/journal.jsonl") does not hint that the path is relative.

### Phase 10 — dependency integrity

D1. **validate-depends-targets-at-ingest** — **Bug.** A `@depends=` naming an
   item that does not exist in the todo file blocks the dependent item
   **permanently and silently**. `select._is_done()` resolves a target via
   `by_id.get(item_id)` and returns `False` when it is absent, so a missing
   target is indistinguishable from an unfinished one: the item lands in
   `blocked` with an ordinary "waiting on X" reason and no indication that X
   was never there. A single typo turns into `Nothing eligible to work on.`
   forever, and a scheduled run reports that as success (AC2.10) every firing.

   The inconsistency is the giveaway: a dependency **cycle** is a startup error
   with the offending ids named (`CycleError`), while a dependency on a
   nonexistent id is not an error at all. Both are malformed input and both are
   detectable at the same moment.

   Validate at ingest: every `@depends=` target must resolve to an item in the
   same file. An unresolvable target is a startup error naming the item, the
   missing id, and the line number. Include a "did you mean" suggestion when a
   target is within a small edit distance of a real `@id=`; a typo is the
   overwhelmingly likely cause.

   *Validation:* `make check` + `pytest tests/test_ingest.py tests/test_select.py -q`
   — an unresolvable `@depends=` exits non-zero before any agent call, names the
   missing id and the line; a valid forward reference to an item later in the
   file still resolves (order must not matter); an item that depends on a `- [x]`
   completed item is still eligible; the existing cycle error is unchanged.
   *Closes:* gap in AC2.x — proposed AC2.12. USER-side spec amendment.
   *Branch slug:* `validate-depends-targets-at-ingest`

   **Deliberately not doing: cross-file dependencies.** `@depends=` resolving
   across todo files would make one file's eligibility depend on another file's
   run history — a much larger change to the model. Two related lists should
   state their run order in prose. Do not plan this without a concrete need.

### Phase 13 — rename the project

N1. **choose-the-name** — `GetStuffDone` names the aspiration, and the
   aspiration is the least distinctive thing about it. Every task runner claims
   to get stuff done. What this one does is **refuse to advance on unproven
   work**, and the name should point at the mechanism.

   **Collision checks run 2026-08-10** (PyPI JSON API, GitHub profile pages,
   domain fetch — all verified directly):

   Previously identified as free (2026-08-09 plan note) — **now both taken**:

   - **Detent** — TAKEN: `pypi.org/project/detent` v1.2.0 exists; summary: "A
     verification runtime that intercepts AI coding agent file writes, runs them
     through a configurable verification pipeline, and rolls back atomically on
     failure." A direct conceptual competitor that reached for the same name.
   - **Pawl** — TAKEN: `pypi.org/project/pawl` v0.1.1 exists; summary:
     "Python API Wrapper - LinkedIn."

   Also taken (checked 2026-08-10): `sprag`, `attest`, `turnstile`, `proven`,
   `vouch`, `latch`, `warrant`, `ratify`, `backstop`, `notch`, `surety`,
   `indexer`, `escapement`, `holdfast`, `escrow`, `steplock`, `certus`, `sentry`,
   `snick`, `foothold`, `lockstep`, `cleat`, `clack`, `clicker`, `stampede`,
   `tollgate`, `proof`, `rein`, `bridle`, `stepwise`, `ascender`, `prusik`
   (notably: prusik v0.202.0 is "evidence-based build harness for autonomous
   coding agents — phase gates that demand captured proof" — another convergent
   competitor).

   **Available on PyPI (404 confirmed 2026-08-10):**

   - **jumar** — A Jumar is a mechanical rope ascender: a clamp that slides
     forward on a rope but locks against backward slip. One-way motion, each
     advance committed before the next. Exact ratchet metaphor. `jumar run`,
     `jumar plan`, `jumar resume` all flow naturally. *GitHub:* a personal user
     `github.com/jumar` exists (keyboard hobbyist in Montreal, no org conflict).
     *jumar.dev:* ECONNREFUSED — needs direct registrar check before committing.
   - **proofstep** — "proof step" as used in formal verification (Lean, Coq):
     each step in a proof must be verified before the next is accepted. Accurate
     and precise. `proofstep run` is slightly awkward; `pst` could alias it.
     *GitHub:* `github.com/proofstep` returned 404 (not found). *proofstep.dev:*
     ECONNREFUSED — needs direct registrar check.
   - **pall** — archaic form of "pawl"; free on PyPI. *pall.dev:* for sale on
     GoDaddy. Not recommended: strong English connotations of gloom ("casting a
     pall") that clash with a productivity tool.
   - **prograde** — free on PyPI; GitHub user exists; means "forward motion"
     (astronomy). Doesn't convey the verification gate. Not recommended.

   **Disqualified despite PyPI availability:**
   - `wicket` — Apache Wicket (Java web framework) is too prominent an
     association; GitHub user exists with no repos.
   - `cadence` — Cadence Design Systems owns `github.com/cadence` and
     `cadence.com`.
   - `stepstone` — `github.com/stepstone` is the German job-board company.
   - `ratch` — free on PyPI; `ratch.dev` is registered (decommissioned service,
     live landing page from Purrso).

   **Decision: jumar**, with the condition that the human checks
   `jumar.dev` directly at a domain registrar before N2 begins. If jumar.dev
   is taken, `proofstep` is the fallback (check `proofstep.dev` too). The human
   must also confirm that the GitHub personal user at `github.com/jumar` does not
   create a branding conflict — the usual pattern is to create `github.com/jumar-dev`
   or `github.com/jumar-run` as the org.

   *Deliverable:* this recorded decision with the collision checks actually run.
   Not a code change.

N2. **rename-everything** — one branch, one commit, mechanical. Do it **before
   anything is published**; the cost roughly doubles once a package name exists
   on an index and a schedule exists on someone's machine.

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

   *Validation:* `make check` + a grep gate — `grep -ri 'gsd\|getstuffdone\|GetStuffDone' src tests specs tools *.md *.toml Makefile` returns only deliberate historical references. Add that grep to CI so the old name cannot creep back in.
   *Closes:* nothing — pure rename. `specs/` are touched, so this is a
   `plan`/`update` beat or a human edit, **not** a `build` iteration.
   *Branch slug:* `rename-everything`

   Sequencing note: N1 and N2 are now unblocked (all in-flight branches merged
   as of 2026-08-10). Do N2 **before** Phase 12's `friendly-run-ids`, so the
   new id format ships under the final name.

### Phase 12 — run ids a human can type

F1. **friendly-run-ids** — `run_id` is `str(uuid.uuid4())`
   (`cli.py:210`, `cli.py:582`), so every reference to a run looks like
   `9c23ddee-8263-477b-a98a-99efff7540b6`. It is unique and it is useless: you
   cannot type it, cannot tell two apart at a glance, cannot tell *when* a run
   happened or *what it was about*, and `ls runs/` sorts them in an order with
   no meaning. Every `resume` and `report` invocation becomes a copy-paste from
   scrollback — and if the scrollback is gone, an `ls -t` and a guess.

   **Design constraint to respect, not fight.** The id is minted *before*
   `ingest()` and `select_next()` — the run directory and journal must exist so
   that `run_started` can record the captured `now` before anything else
   happens. So the selected item's slug is **not knowable** when the id is
   chosen. Do not try to rename the directory after selection: the journal is
   already open, and a rename breaks resume-by-id for anything that recorded
   the original.

   Four changes, in increasing order of effort. The first two are most of the
   benefit:

   1. **Accept an unambiguous prefix** wherever a run id is taken (`resume`,
      `report`). `gsd resume 9c23` should work. Ambiguous prefix ⇒ error listing
      the candidates. This alone makes the current ids tolerable and works on
      every run that already exists.
   2. **Accept `latest`** as a run id, resolving to the most recently started
      run in the runs directory. `gsd resume latest --retry-failed` is the
      command this whole phase exists to enable.
   3. **Change the format** to `<YYYYMMDD>-<HHMM>-<4 random chars>`, e.g.
      `20260809-1543-a3f9`, derived from the journalled `now` — via `clock.py`,
      not the wall clock, or it breaks the one-clock rule and every frozen-clock
      test. Sorts chronologically under a plain `ls`, greps by date, and the
      random tail keeps two runs in the same minute distinct.
   4. **Record the item in a run index.** Since the slug cannot go in the
      directory name, write `runs/index.tsv` — one line per run: id, started,
      item_id, status — appended at `run_started` and updated at `run_finished`.
      That is what lets `gsd status` and a future `gsd runs` answer "which run
      was the ASF one?" without opening twelve journals.

   **Backwards compatibility is not optional.** Existing runs are uuid-named and
   must stay resumable. Prefix matching gives that for free; do not add a
   migration that renames anything.

   *Validation:* `make check` + `pytest tests/test_resume.py tests/test_report.py -q`
   — a new run id matches `^\d{8}-\d{4}-[a-z0-9]{4}$`; two runs started in the
   same minute get distinct ids; an existing uuid-named run still resolves;
   an unambiguous prefix resolves and an ambiguous one errors naming the
   candidates; `latest` resolves to the most recent by `run_started`, not by
   filesystem mtime; the id in the report header matches the directory.
   *Closes:* new behaviour — USER-side spec amendment alongside the run-journal
   contract in `specs/03-data-model.md`.
   *Branch slug:* `friendly-run-ids`

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
- When `failure-backoff-and-park` lands, add the proposed AC2.11 (select
  excludes `@paused=` items, reported as Parked) and AC8.8 (failed terminal
  state advances `@failed=`; threshold appends `@paused=auto-failures`) to
  `specs/02-functional-spec.md`.
- When `gsd-status` / `json-output` land, add the corresponding contract
  sections (proposed AC11.x) to `specs/02-functional-spec.md`.
- Add AC5.6 (progress output on stderr; silent under `--json` and off-TTY) to
  `specs/02-functional-spec.md` §Stage 5.
- Add the Phase 6c check-shape rules to `specs/02-functional-spec.md` §Stage 3
  and §Stage 6 as acceptance criteria (proposed AC3.7 shell wrapper refused,
  AC3.8 check must be falsifiable, AC6.8 zero-byte file fails, AC6.9 denied
  argv is `inconclusive` and spawns nothing). Until they are in `specs/`, the
  `plan` beat will keep re-deriving them as gaps.
- Add a `[harness.<stage>]` section to the config reference in `USAGE.md` when
  M1 lands, and record the model-selection guidance (plan high, draft cheap,
  judge independent) somewhere a user will find it.
- Amend the security posture section of `specs/`, `README.md` and `USAGE.md`
  to the send-boundary policy; they still say `network` is not a default
  capability and that `curl`/`wget` are denied.
- When `resume-can-retry-a-failed-item` lands, add proposed AC-S5 to
  `specs/02-functional-spec.md` §Cross-cutting.
- When `friendly-run-ids` lands, update the `Run.run_id` format note in
  `specs/03-data-model.md` §Run to match the new `<YYYYMMDD>-<HHMM>-<4 chars>`
  format and document prefix-matching and `latest` resolution.
