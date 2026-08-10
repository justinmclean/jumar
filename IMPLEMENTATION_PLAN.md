<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-10

Phases 0–8 complete and merged to main (including failure-backoff-and-park,
gsd-status, json-output, and run-progress-output).

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

---

## Work items (priority order)

### Phase 6b — enforce the security controls that are only documented

**Highest priority.** These findings came from a code audit rather than from a
spec, so every `plan` beat regeneration drops them. **The durable fix is to add
them to `specs/02-functional-spec.md` as acceptance criteria** — a USER-side
edit. Until that happens, expect to re-add this section after each plan beat.

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

### Phase 6c — checks that cannot be hollow

**Ahead of any further trial runs.** Source: a real run of `agents-md-legal`
on 2026-08-08. Fifteen subtasks, fifteen passes, item reported success — and
two of the three source documents had never been downloaded. The drafts were
written from the model's memory of the AI Act.

Nothing in the pipeline was broken. Every stage did what it was specified to
do. The **checks** were hollow, and a verification system whose checks are
authored by the same model that does the work has no defence against that
unless the check *shape* is constrained. Key failures: 14 of 15 checks were
`["bash", "-c", "grep -q … file"]` (defeating argv allow/deny simultaneously);
the AI Act fetch returned HTTP 202 with a 0-byte file (curl exited 0, `test -f`
passed); several checks grepped a word the executing agent had just written.

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

Prices checked 2026-08-09 (per MTok, input/output): Fable 5 $10/$50,
Opus 5 $5/$25, Sonnet 5 $2/$10 (**rising to $3/$15 on 1 Sept 2026**),
Haiku 4.5 $1/$5. Re-check before acting on the arithmetic; these move.

The observation: **spend and leverage sit in different stages.** A 15-subtask
item produces ~70k output tokens, almost all in `execute`. `decompose` is one
call of a few thousand tokens — and it authors every check for the whole item.
Every failure in the 2026-08-08/09 trial runs was a decomposition failure;
the drafts themselves were serviceable.

M1. **per-stage-model-selection** — `HarnessConfig` carries a single
   `agent`/`model` pair, consumed identically by `decompose.py`, `execute.py`,
   `verify/judge.py` and (via `execute`) `repair.py`. There is no way to
   express "plan with Opus, draft with Sonnet". Add optional per-stage overrides
   falling back to the top-level default:

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
   each plan and attempt. Today `HarnessInfo` is built from `config.harness` at
   four call sites; they become one `config.harness.for_stage("decompose")`
   lookup.

   Independence matters as much as capability for the judge. An adversarial
   verifier running the same model that produced the artefact shares its blind
   spots. The config should make "judge on a different model from execute"
   expressible; whether to *warn* when they match is probably no — a warning
   nobody can act on is noise.
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

   Build the `HarnessInfo` from config (per M1, the judge stage's resolved
   model) and pass it at both call sites. Also pass `judge_timeout_s` from
   config rather than the dataclass default.
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

### Phase 12 — run ids a human can type

F1. **friendly-run-ids** — `run_id` is `str(uuid.uuid4())`
   (`cli.py:220`, `cli.py:611`), so every reference to a run looks like
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

### Phase 13 — rename the project

N1. **choose-the-name** — `GetStuffDone` names the aspiration, and the
   aspiration is the least distinctive thing about it. Every task runner claims
   to get stuff done. What this one does is **refuse to advance on unproven
   work**, and the name should point at the mechanism.

   Candidates already checked for collisions (2026-08-09): **Ratchet** is taken
   on PyPI *and* `getratchet.dev` is an AI-agent accountability product —
   someone reached for the same metaphor for the same problem. **Assay** collides
   with `brandon-rhodes/assay`, a Python testing framework. **Belay** and
   **Piton** are taken on PyPI. **Detent** (the catch that holds a mechanism in a
   defined position until deliberately released) and **Pawl** (the tooth that
   permits motion one way and blocks the other) both appear free. Detent reads
   better aloud; that matters for something typed and spoken.

   Before committing: check PyPI, the GitHub org, and the `.dev` domain
   **directly**. Absence from search results is weak evidence.

   *Deliverable:* a decision, recorded here with the collision checks that were
   actually run. Not a code change.

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

   *Validation:* `make check` + a grep gate — `grep -ri 'gsd\|getstuffdone\|
   GetStuffDone' src tests specs tools *.md *.toml Makefile` returns only
   deliberate historical references. Add that grep to CI so the old name cannot
   creep back in.
   *Closes:* nothing — pure rename. `specs/` are touched, so this is a
   `plan`/`update` beat or a human edit, **not** a `build` iteration.
   *Branch slug:* `rename-everything`

   Sequencing note: do N1 and N2 **after** the current in-flight branches merge
   and **before** Phase 12's `friendly-run-ids`, so the new id format ships
   under the final name.

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
  `plan` beat will keep dropping Phase 6b/6c from this file.
- Add a `[harness.<stage>]` section to the config reference in `USAGE.md` when
  M1 lands, and record the model-selection guidance (plan high, draft cheap,
  judge independent) somewhere a user will find it.
- Amend the security posture section of `specs/`, `README.md` and `USAGE.md`
  to the send-boundary policy; they still say `network` is not a default
  capability and that `curl`/`wget` are denied.
