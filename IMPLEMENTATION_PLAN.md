<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — Jumar (formerly GetStuffDone)

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-11

**N2 (rename-everything) completed 2026-08-11.** The project is now **jumar**
throughout: package `src/jumar/`, CLI verb `jumar`, config `jumar.toml` with
`[jumar]` sections (clean break, old names not read), lock `.jumar.lock`,
schedule marker `# jumar-meta: ` and launchd label `com.jumar.<id>`.
`jumar doctor` gained a legacy-schedule check that detects and names
pre-rename crontab/launchd entries. README and USAGE carry the migration
note; the old-name grep gate runs in CI (lines carrying pre-rename literals
on purpose are tagged `legacy-name-ok`). Existing `runs/` journals untouched.
The GitHub remote was created as `justinmclean/jumar` (private until ready).

**RESOLVED 2026-08-11:** the blocker recorded earlier today is cleared. All
three local branches (`runs-dir-relative-path`, `status-json`, `run-index-tsv`)
are merged to main and the branches deleted. Post-merge incident: the
`status-json` and `run-index-tsv` merges were first committed with unresolved
conflict markers in `cli.py` and `test_resume.py` (199 ruff errors); fixed by a
follow-up commit that resolved the conflicts properly (kept the CWD-aware
`_resolve_run_id`, grafted in the index fast path for `latest`, kept the
`--json` additions and both new test sections). lint and mypy verified green.

The two pieces of formerly uncommitted work on main are also committed:
1. `--strict-mcp-config` harness work (`src/getstuffdone/harness.py` +
   `tests/test_harness_argv.py`) — committed directly to main (91f87c6); the
   planned `block-mcp-server-inheritance` branch was not used.
2. `IMPLEMENTATION_PLAN.md` — the plan updates (Phase 14 items, in-flight list),
   committed 2026-08-11 (3b293c1) and superseded by this revision.

All planned phases through Phase 13 (N1 + N2) and Phase 12 F1 + F2 are merged
to main, plus Phase 14 P3 (reuse-the-execution-session) and the
implementation-guide-run-context write-up (both merged 2026-08-11). The only
open work item is Phase 14 P2 — blocked on its design decision.

- C1 (`thread-check-rejection-reason`), R1 (`resume-can-retry-a-failed-item`),
  D1 (`validate-depends-targets-at-ingest`), N1 (`choose-the-name`), and
  F1 (`friendly-run-ids`) all landed on 2026-08-10 and 2026-08-11.
- N1 decision: **jumar** (PyPI 404 confirmed 2026-08-10; branding coexistence
  with existing "jumar" uses accepted by the human 2026-08-11; only the
  `jumar.dev` registrar check remains before N2 begins).
- F1 shipped items 1–3 (prefix matching, `latest`, new id format). Item 4
  shipped separately as F2 (`run-index-tsv`), merged 2026-08-11.
- **Sequencing note (resolved):** N2 shipped 2026-08-11, before anything was
  pushed to the (private) remote — the "rename before publication" rule held.
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
- **block-mcp-server-inheritance** — `--strict-mcp-config` in the Claude harness argv so user-configured MCP servers (e.g. a mail server) cannot bypass the send-boundary deny list. Landed 2026-08-11 directly on main.
- **runs-dir-relative-path** — `_resolve_run_id` + `_RunResolveError` in `cli.py`; prefix matching and `latest` wired into `gsd resume` and `gsd report`; CWD-aware error messages; C1 follow-up (rejection detail in decompose retry). Merged 2026-08-11.
- **status-json** — `--json` flag on `gsd status` (AC11.5, AC11.6); run-id symbol correction from F1. Merged 2026-08-11; closes the known gap noted in `specs/02` §Stage 11.
- **run-index-tsv** — F2. `runs/index.tsv` appended at `run_started`, status column updated at `run_finished`; `_resolve_run_id` uses the index for `latest` when present, falls back to journal scan. Merged 2026-08-11.
- **implementation-guide-run-context** — §4 "What happens during a run" in `USAGE.md`: progress output, report, status view, agent claims, verifier evidence, repair attempts and next action, read together. Merged 2026-08-11.
- **reuse-the-execution-session** — P3. One execution session per item (`--session-id`/`--resume`), minted at `plan_created` and journalled; repair continues the same session; verifiers and judge always get a fresh context; harnesses without resume fall back to today's argv. Merged 2026-08-11.
- **rename-everything** — N2. Full rename GetStuffDone/`gsd` → **jumar**: package dir, entry point, CLI verb, `jumar.toml` + `[jumar]` config (clean break), `.jumar.lock`, `# jumar-meta: ` / `com.jumar.<id>` schedule markers, docs, specs, spec-loop prompts. `doctor` detects pre-rename schedule entries; CI grep gate blocks the old name. Landed 2026-08-11.

---

## Work items (priority order)

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

- **Register `jumar.dev`** (or chosen domain) if wanted — no longer a blocker:
  the rename shipped 2026-08-11 at the human's direction with branding
  coexistence accepted; the domain is now a nice-to-have, not a gate.
- Confirm which agent CLI is on PATH before the first loop run
  (`SPEC_LOOP_AGENT`, default `claude`).
- Run the loop inside a sandbox with no push credentials in the environment.
- Regenerate the `USAGE.md` transcripts against the current build — §2 and §4
  still show uuid-era run ids, and the doc promises real output.

Done 2026-08-11 (second doc pass): README `--json` coverage corrected to
plan/run/report/status; USAGE command reference gained `gsd status --json` and
a `runs/index.tsv` section (cache only, journals authoritative); `specs/02`
§Stage 11 known gaps marked closed; `specs/04` §Execution isolation records
`--strict-mcp-config`. Still pending: regenerate the USAGE §2/§4 transcripts
(uuid-era run ids) — listed above. The gitignore follow-up was verified already
satisfied (shipped `.gitignore` ignores `todo.md`, `gsd.toml`, `runs/`) and is
removed from this list.

Done 2026-08-11 (every "when X lands" spec/doc amendment — X has landed; see
the Status note at the top for the full list): the proposed ACs are in
`specs/02-functional-spec.md`, the run-id/backoff/config updates in
`specs/03-data-model.md`, and the send-boundary posture, `[gsd.harness.<stage>]`
reference and model-selection guidance in `specs/04-technical-plan.md` /
`USAGE.md`.
