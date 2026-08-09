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

### Phase 6c — checks that cannot be hollow

**Ahead of any further trial runs.** Source: a real run of `agents-md-legal`
on 2026-08-08. Fifteen subtasks, fifteen passes, item reported success — and
two of the three source documents had never been downloaded. The drafts were
written from the model's memory of the AI Act.

Nothing in the pipeline was broken. Every stage did what it was specified to
do. The **checks** were hollow, and a verification system whose checks are
authored by the same model that does the work has no defence against that
unless the check *shape* is constrained. Details of the run:

- 14 of 15 checks were `["bash", "-c", "grep -q … file"]`. That is a shell
  string wearing an argv: it defeats `shell=False`, the argv[0] allow list and
  the deny list simultaneously, with one array element.
- The AI Act fetch returned HTTP 202 with no body. `curl` wrote a 0-byte file
  and exited 0. The check was `test -f`, which passed.
- Several checks grepped a single word out of a file the executing agent had
  just written, which proves the agent typed the word, not that the work is
  correct.

H1. **reject-shell-wrapper-checks** — `Check.__post_init__` refuses
   `kind=command` whose `argv[0]` basename is a shell (`bash`, `sh`, `zsh`,
   `dash`, `ksh`, `fish`, `csh`, `tcsh`) **and** which passes an inline-program
   flag (`-c`, `-lc`, `-ic`, `--command`). A wrapper running a *file*
   (`["bash", "script.sh"]`) is still an argv and stays legal. The rejection
   message names the offending prefix and shows the argv form. Mirrored as an
   explicit rule in the decompose prompt so plans do not have to fail first.
   *Validation:* `make check` + `pytest tests/test_hollow_checks.py -q`

H2. **checks-must-be-falsifiable** — `Check.__post_init__` refuses a
   `kind=command` whose `argv[0]` cannot fail for any reason connected to the
   work: `true`, `:`, `echo`, `printf`, `pwd`, `date`, `sleep`, `yes`,
   `whoami`, `hostname`, `uname`, `id`, `env`, `clear`; plus `ls`, `test` and
   `[` when given no operands (flags are not operands). Decompose prompt gains
   the corresponding rule, phrased as a question the planner must answer —
   *what would make this check return non-zero?*

   **Deliberately NOT implemented, and this is the honest part:** the observed
   `grep -q <single word>` pattern is *not* rejected. There is no lexical test
   that separates `grep -q OK marker.txt` (a legitimate check) from
   `grep -q draft notes.md` (proof that a word was typed) without false
   positives that would refuse real checks — and refusing real checks pushes
   the planner toward `kind=judge`, which is weaker. The mitigations shipped
   instead are the decompose-prompt rule "check the outcome, not the artefact
   of your own effort" and the zero-byte rule in H3. **The residual risk is
   real and unclosed:** a determined-to-be-agreeable planner can still author a
   check that passes without the work being right. The judge verifier is where
   a future fix belongs — grade the *check* adversarially at plan time, not
   just the result at verify time. Do not close this item silently.

H3. **fetch-failure-must-fail-the-subtask** — `verify/file.py` returns
   `failed` for a path that exists but is 0 bytes, with `size_bytes: 0` and
   `error: file_is_empty` in the evidence, before any pattern matching. A
   failed download is the canonical producer of this state. Decompose prompt
   gains: assert content via `pattern`, not existence.

H4. **enforce-command-allowlist** *(completes S1 below)* — `is_allowed()` is
   now called by `verify/command.py` before `subprocess.run`. A denied argv is
   `inconclusive` with `capability_denied` evidence and **spawns no process**
   (asserted by monkeypatching `subprocess.run` to raise). `VerifyContext`
   gained `config: Config | None`, threaded from `cli.py` and `repair.py`; a
   context without config falls back to the default policy, never to allow-all.

   Policy change landed with it, per the 2026-08-08 decision: `network` is a
   default capability, `curl`/`wget` moved from deny to allow, and the deny
   list is now the *send* vectors (`mail`, `mailx`, `sendmail`, `ssmtp`,
   `msmtp`, `ssh`, `scp`, `sftp`, `rsync`). Six tests in `test_config.py`
   asserted the superseded policy and were **inverted, not deleted** — they now
   assert the new contract, and `test_python2_style_invocation_is_allowed`
   carries the reason the list is defence in depth rather than a boundary.

*Follow-up not done here:* `_build_check` returns `None` for a rejected check,
so the retry prompt says "missing check" rather than naming the rule that was
broken. Threading the `ValueError` message into the retry would make the
second attempt much more likely to succeed. Small, worth doing.

### Phase 9 — model selection per stage

Prices checked 2026-08-09 against `platform.claude.com/docs/en/about-claude/pricing`
(per MTok, input/output): Fable 5 $10/$50, Opus 5 $5/$25, Sonnet 5 $2/$10 —
**introductory, rising to $3/$15 on 1 Sept 2026** — Haiku 4.5 $1/$5. Re-check
before acting on the arithmetic below; these move.

The observation driving both items: **spend and leverage sit in different
stages.** A 15-subtask item produces ~70k output tokens, almost all of it in
`execute`. `decompose` is one call of a few thousand tokens — and it authors
every check for the whole item. Every failure in the 2026-08-08/09 trial runs
was a decomposition failure (shell-wrapper checks, `test -f` on a zero-byte
file, `Art 2(12)` against a draft saying `Article 2(12)`); the drafts
themselves were serviceable. Paying frontier rates for the plan call costs
cents and decides whether the following 70k tokens are verified or theatre.
Paying them for the drafting bulk is where the money actually goes.

M1. **per-stage-model-selection** — `HarnessConfig` carries a single
   `agent`/`model` pair, consumed identically by `decompose.py`, `execute.py`,
   `verify/judge.py` and (via `execute`) `repair.py`. There is no way to
   express "plan with Opus, draft with Sonnet", which is the configuration the
   cost/leverage split calls for. Add optional per-stage overrides falling back
   to the top-level default:

   ```toml
   [harness]
   agent = "claude"
   model = "sonnet"          # default for every stage

   [harness.decompose]
   model = "opus"            # authors the checks; highest leverage per token

   [harness.judge]
   model = "opus"            # see M2 and the independence note below
   ```

   Resolution is `stage override → [harness] default → built-in default`, and
   the **resolved** model must be recorded in the `HarnessInfo` journalled with
   each plan and attempt, so a run's report says which model did which stage.
   Today `HarnessInfo` is built from `config.harness` at four call sites; they
   become one `config.harness.for_stage("decompose")` lookup.

   Independence matters as much as capability for the judge. An adversarial
   verifier running the same model that produced the artefact shares its blind
   spots — Sonnet judging Sonnet's own prose is the weakest link in the design.
   The config should make "judge on a different model from execute" expressible;
   whether to *warn* when they match is a judgement call, and the answer is
   probably no — a warning nobody can act on is noise.
   *Validation:* `make check` + `pytest tests/test_config.py tests/test_decompose.py -q`
   — a stage override is used for that stage only; an absent override falls back;
   the resolved model reaches the journalled `HarnessInfo`; an unknown stage key
   in `gsd.toml` is a startup error, not a silent no-op.
   *Closes:* new behaviour — USER-side spec amendment alongside the harness
   contract in `specs/04-technical-plan.md`.
   *Branch slug:* `per-stage-model-selection`

M2. **wire-the-judge-harness** — **Bug, and it makes `kind=judge` useless in a
   real run.** `run_item` in `cli.py` constructs `VerifyContext` without
   `harness=`, and so does `repair.py`. `verify/judge.py` returns
   `inconclusive` with `no_harness_configured` when `ctx.harness is None` — so
   every judge check in a `gsd run` is inconclusive by construction, routes
   straight to repair, exhausts the budget and fails the item. Nothing detects
   this: the judge tests build a `VerifyContext` with a harness by hand, so the
   verifier is correct in isolation and unreachable in practice.

   This is the verifier the decompose prompt names as the fallback "when no
   executable check is possible", so on judgement-heavy items it is the one
   most likely to be chosen. Build the `HarnessInfo` from config (per M1, the
   judge stage's resolved model) and pass it at both call sites. Also pass
   `judge_timeout_s` from config rather than the dataclass default.
   *Validation:* `make check` + `pytest tests/test_run.py -q` — an end-to-end
   run whose plan contains a `judge` check reaches the judge verifier with a
   harness and does **not** return `no_harness_configured`. The existing
   isolated judge tests stay as they are.
   *Closes:* AC6.4 in practice — currently satisfied at unit level only.
   *Branch slug:* `wire-the-judge-harness`

   Do M2 before M1 if only one gets done. M1 is tuning; M2 is a verifier that
   silently never runs.

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
   missing id, and the line number — the same treatment cycles already get.
   Include a "did you mean" suggestion when a target is within a small edit
   distance of a real `@id=`; a typo is the overwhelmingly likely cause and the
   suggestion costs nothing.

   *Validation:* `make check` + `pytest tests/test_ingest.py tests/test_select.py -q`
   — an unresolvable `@depends=` exits non-zero before any agent call, names the
   missing id and the line; a valid forward reference to an item **later in the
   file** still resolves (order must not matter); an item that depends on a
   `- [x]` completed item is still eligible; the existing cycle error is
   unchanged.
   *Closes:* gap in AC2.x — the dependency gate is specified for satisfied and
   unsatisfied targets but not for absent ones. USER-side spec amendment
   (proposed AC2.12).
   *Branch slug:* `validate-depends-targets-at-ingest`

   **Deliberately not doing: cross-file dependencies.** `@depends=` resolving
   across todo files would need `done_ids` populated from run journals in
   `plan` and `run`, which today only `resume` does — and it would make one
   file's eligibility depend on another file's run history, which is a much
   larger change to the model than it first appears. Two related lists should
   state their run order in prose and be run by hand in that order. Do not plan
   this without a concrete need.

---

## Guardrails (do not re-plan these)

- **Verification is not optional and not deferrable.** No work item may ship a
  stage that performs work ahead of the check that proves it worked.
  `execute-and-verify-command` (now merged) was deliberately two modules in one
  branch for this reason; the same rule applies to any future item that adds an
  execution path.
- **A check may never be weakened to pass.** Not a test, not an AC, not a
  subtask's own `Check` at runtime (that is what AC7.3 exists to enforce).
- **The enforced boundary is *send*, not *fetch*** (decided 2026-08-08).
  `network` IS a default capability and `curl`/`wget` are allowed — an agent
  that cannot fetch a primary source writes from memory instead, which is the
  worse failure and is exactly what happened on 2026-08-08. `mail`, `mailx`,
  `sendmail`, `ssmtp`, `msmtp`, `ssh`, `scp`, `sftp`, `rsync` are denied, and
  `git push` / `gh` stay hard-denied in every dispatched argv. Do not plan a
  work item that relaxes the send boundary — and do not plan one that claims
  the allow list is a sandbox: with `python3` allowed and the network
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
- Do not install a recurring schedule against a real todo list until
  `failure-backoff-and-park` has landed — until then a failing item is
  re-attempted (and its agent budget re-spent) on every firing.
- When `failure-backoff-and-park` lands, add the proposed AC2.11 (select
  excludes `@paused=` items, reported as Parked) and AC8.8 (failed terminal
  state advances `@failed=`; threshold appends `@paused=auto-failures`) to
  `specs/02-functional-spec.md` — the loop may not edit specs itself.
- When `gsd-status` / `json-output` land, add the corresponding contract
  sections (proposed AC11.x) to `specs/02-functional-spec.md` — same rule.
- Add AC5.6 (progress output on stderr; silent under `--json` and off-TTY) to
  `specs/02-functional-spec.md` §Stage 5 — `run-progress-output` is merged and
  has no AC behind it.
- Add the Phase 6c check-shape rules to `specs/02-functional-spec.md` §Stage 3
  and §Stage 6 as acceptance criteria (proposed AC3.7 shell wrapper refused,
  AC3.8 check must be falsifiable, AC6.8 zero-byte file fails, AC6.9 denied
  argv is `inconclusive` and spawns nothing). Until they are in `specs/`, the
  `plan` beat will keep dropping Phase 6b/6c from this file — that has now
  happened four times.
- Add a `[harness.<stage>]` section to the config reference in `USAGE.md` when
  M1 lands, and record the model-selection guidance (plan high, draft cheap,
  judge independent) somewhere a user will find it.
- Amend the security posture section of `specs/`, `README.md` and `USAGE.md`
  to the send-boundary policy; they still say `network` is not a default
  capability and that `curl`/`wget` are denied.
