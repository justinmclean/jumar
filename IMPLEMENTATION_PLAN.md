<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation Plan — Jumar (formerly GetStuffDone)

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-22

**Full spec-vs-code reconciliation pass.** Every spec in `specs/` was read in
full and compared against `src/jumar/` and `tests/` (parallel subagents plus
direct greps/reads to confirm each finding — not inferred from routing
snapshots). `git log`/`git branch -a` confirm every remote branch
(`local-model-harness`, `plan-phase-15-local-models`, `fix/harness-outage-budget`,
`contributor-onramp`, `repo-polish`, both `copilot/*` branches) is fully merged
into `main` (`git log main..origin/<branch>` is empty for all of them) and
there are no open PRs and no local work-item branches — nothing in flight.
`make check` is green on `main` (94.64% coverage) as of this pass.

**Newly landed since the 2026-08-11 status note (all merged to `main`,
previously undocumented here):**

- **local-model-harness** — Phase 15, L1. `src/jumar/openai_agent.py`: an
  in-process harness (`agent = "openai"`) driving any OpenAI-compatible
  `/chat/completions` endpoint (LM Studio, llama.cpp, vLLM) with its own
  `read_file`/`write_file`/`run_command` tool loop, gated by the same
  `Capability`/`is_allowed()` checks as every other stage — no
  `allow_unrestricted_harness` needed. `harness.py` gained
  `IN_PROCESS_HARNESSES`; `config.py` gained `base_url`/`api_key_env`;
  `doctor.py` probes `/models`. **This item is done — do not re-plan it.**
  `specs/04-technical-plan.md` §Harness still describes only the six
  agent-CLI harnesses and doesn't mention the in-process route; that's a
  human/`update`-beat doc sync, not a build gap.
- **harness-outage-budget-exclusion** — `harness.detect_harness_error()`
  classifies a usage-limit hit / auth failure / missing binary as
  harness-level, distinct from an ordinary failed check; `backoff.py`'s
  `advance_failure_count()` no-ops on it so a healthy item is never parked by
  an outage. Journalled as `harness_error` (already in `specs/03`'s event
  list).
- **session-uuid-pinning** — `clock.make_session_id()` now mints a real UUID4;
  the first subtask creates the session with `--session-id`, later subtasks
  and repairs `--resume` it. Fixes a real bug (every multi-subtask item's
  second subtask previously failed outright).
- **contributor-onramp-docs** — CONTRIBUTING.md, CODE_OF_CONDUCT.md,
  CHANGELOG.md, issue/PR templates, SECURITY.md, and a `make check` + tag
  guard in the publish workflow. Not a `src/jumar/` change; no work item was
  needed and none is added retroactively.

**New gaps confirmed this pass** (each verified by direct file read/grep, not
assumed — see the work items below for the exact evidence): Stage 10's
promised `systemd` scheduler backend does not exist (only cron/launchd);
`select.py` computes deferred items but never journals the documented
`item_deferred` event; a totally malformed todo line (fails the task-list
regex) produces no parse warning, narrower than AC1.6; and AC7.4's
`--halt-on-fail` behaviour is unwired end to end — see the decision-gated item
below before anyone touches it.

**Spec text that has drifted from a *deliberate* decision, not a code gap —
flagged for a human/`update`-beat pass, not a build work item** (no code
change would close these; the spec prose is what's stale):
- `specs/01-product-spec.md` still says "Least authority… default to a
  sandboxed, no-network… execution context." `network` has been a default
  capability since the 2026-08-08 send-not-fetch decision (`config.py`
  `_DEFAULT_CAPABILITIES`), and `gate.py`'s own docstring says the capability
  check "does not sandbox the agent's process or restrict OS calls at
  runtime." Both are already correct in `specs/04` and `USAGE.md` — only `01`'s
  wording is stale.
- `specs/03-data-model.md`'s `Run`, `ItemResult`, `RunLock`, and
  `ScheduleEntry` shapes list fields (`interactive`, `trigger`, `now`, `tz`,
  `eligible_at`, `was_overdue`, `next_occurrence`, `hostname`, `acquired_at`,
  `backend`, `command`, `log_path`, `installed_at`) and names
  (`runs/.lock-<hash>`, `cron`, `tz`) that the real `models.py`/`lock.py`
  (`Lock` → `.jumar.lock`)/`schedule.py` (`cron_expr`, `timezone`) don't match.
  Code also carries undocumented fields (`Plan.session_id`; `HarnessInfo.
  base_url/api_key_env/commands_allow/commands_deny`). None of this is a
  functional bug — it's a doc sync for `update`.
- AC1.3's wording ("flagged `decomposition=authored`") overstates what's
  stored — `TodoItem` has no `decomposition` field; `decompose.py` infers it
  from `bool(item.authored_subtasks)`. Behaviour is correct; the AC's prose
  names a field that was never built that way.

---

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
- **local-model-harness** — L1 (Phase 15). `src/jumar/openai_agent.py`: in-process `agent = "openai"` harness against an OpenAI-compatible `/chat/completions` endpoint, its own gated `read_file`/`write_file`/`run_command` tool loop, no `allow_unrestricted_harness` needed; `harness.py`'s `IN_PROCESS_HARNESSES`; `config.py`'s `base_url`/`api_key_env`; `doctor.py`'s `/models` probe. Merged 2026-08-22 (PR #10). See the 2026-08-22 status note for the doc-sync this leaves open.
- **harness-outage-budget-exclusion** — `harness.detect_harness_error()` + `backoff.advance_failure_count()`: a harness-level outage (usage-limit hit, auth failure, missing binary) no longer counts against an item's `@failed=` budget or triggers auto-park. Merged 2026-08-18 (PR #8).
- **session-uuid-pinning** — `clock.make_session_id()` mints a real UUID4 and the first subtask creates the session before later subtasks/repairs `--resume` it, fixing a real second-subtask failure. Merged 2026-08-12 (PR #7).

---

## Work items (priority order)

Phase 15 (L1, local-model-harness) is **done** — see the 2026-08-22 status note
and the Completed list. Phase 16 below is this pass's new material, ordered
newest-and-clearest-first; the decision-gated item is last on purpose, same
convention as the closed P2 above.

### Phase 16 — spec-vs-code reconciliation (2026-08-22 pass)

W1. **ingest-warns-on-unparseable-lines** — AC1.6 says "a malformed line does
   not abort ingest; it is recorded as a parse warning" but `ingest.py`'s
   `_finalize` only warns for a line that **matches** `_TASK_RE` with bad
   *content* (bad `@` token, etc.). A line that fails `_TASK_RE` entirely
   (matches none of the task-list/context/heading patterns) is silently
   absorbed as `context` — no warning, contrary to the AC's plain wording.
   Confirmed by reading `ingest.py`'s line dispatch and `tests/test_ingest.py`
   (no test feeds a line that fails every pattern).

   Shape: classify a line that isn't a task item, isn't blank, and isn't
   recognised context/heading syntax as `malformed`, emit a parse warning
   naming the line number and its content, and keep parsing the rest of the
   file (AC1.6's "does not abort ingest" is already true — only the warning is
   missing).

   *Validation:* `make check` + `pytest tests/test_ingest.py -q`, adding a case
   that feeds a line matching none of the recognised patterns and asserts a
   warning is recorded naming the line number, while the items around it still
   parse.
   *Closes:* AC1.6 (the unmatched-line half of it).
   *Branch slug:* `ingest-warns-on-unparseable-lines`

W2. **journal-item-deferred-event** — `specs/03-data-model.md`'s event list
   includes `item_deferred`, and `journal.py` already defines the
   `ITEM_DEFERRED` constant, but grep across `src/jumar/` confirms it is never
   passed to `journal.append()` anywhere. `select.py`'s `select_next()` already
   computes exactly the information the event needs (`SelectionResult.deferred`
   / `.parked`, each an `(item, reason)` pair) — it's just never journalled,
   only printed to stdout in `cli.py`'s "Nothing eligible" branch. Separately,
   `cli.py`'s resume path journals a bare string literal `"retry_started"`
   (line ~1155) that has no constant in `journal.py` and isn't in `specs/03`'s
   event list either — same class of gap, same fix location, worth doing in
   one pass.

   Shape: `cli.py` journals `ITEM_DEFERRED` once per deferred/parked item
   whenever `select_next()` returns no selection (mirroring the existing
   "Nothing eligible" print loop), with the item id and reason in the payload.
   Add a `RETRY_STARTED` constant to `journal.py` and use it in place of the
   bare string at the resume call site.

   *Validation:* `make check` + `pytest tests/test_select.py tests/test_journal.py tests/test_run.py -q` —
   a run with nothing eligible journals one `item_deferred` (or a parked
   equivalent) entry per blocked item; `journal.RETRY_STARTED` exists and
   `tests/test_resume.py`'s retry-started assertions use the constant instead
   of the literal.
   *Closes:* the documented-but-unemitted `item_deferred` event in
   `specs/03-data-model.md`.
   *Branch slug:* `journal-item-deferred-event`

W3. **systemd-schedule-backend** — `specs/02-functional-spec.md` Stage 10 says
   "Backend by platform: `crontab` on Linux/BSD, `launchd` user agent on macOS,
   `systemd --user` timer where available and preferred," and
   `models.ScheduleBackend` already has a `systemd` member — but
   `schedule.py` only defines `CronBackend` and `LaunchdBackend`;
   `default_backend()` branches only on `darwin` vs. everything else, so a
   Linux host with `systemd --user` available silently gets cron instead of
   the "preferred" backend. Confirmed by reading `schedule.py` in full (no
   `SystemdBackend` class, no `systemctl`/`systemd` string anywhere) — this
   gap isn't even named in Stage 10's own "Known gaps" list (which only names
   the Windows omission), so `plan` is recording it fresh rather than closing
   a listed gap.

   Shape: a `SystemdBackend` alongside `CronBackend`/`LaunchdBackend`,
   implementing the same `Backend` protocol — install/list/remove a
   `~/.config/systemd/user/jumar-<schedule-id>.{service,timer}` pair, delimited
   the same way the other backends delimit their entries (AC10.2's contract
   applies identically). `default_backend()` prefers it on Linux when
   `systemctl --user` is reachable (mirroring how it already prefers launchd on
   darwin), falling back to cron otherwise. `doctor.py`'s schedule-readability
   check gains the systemd case alongside its existing cron/launchd branches.

   *Validation:* `make check` + `pytest tests/test_schedule.py -q` extended
   with a `SystemdBackend` suite mirroring the existing `CronBackend`/
   `LaunchdBackend` ones: AC10.1 (dry-run installs nothing), AC10.2
   (marker-delimited install/remove, round-tripped against a fixture unit file
   with unrelated content preserved byte-identical), AC10.3 (absolute paths +
   `--non-interactive`), AC10.7 (list reports only jumar-owned entries), AC10.9
   (resolved timezone recorded) — against a fake filesystem/backend, the same
   pattern the existing suite already uses, not a live `systemctl`.
   *Closes:* Stage 10's "backend by platform" contract line (no single AC
   number is systemd-specific; AC10.1–10.4/10.7/10.9 all generalise to the new
   backend).
   *Branch slug:* `systemd-schedule-backend`

W4. **close-thin-ac-test-coverage** — six acceptance criteria are implemented
   and already have *a* test, but not the literal assertion the AC text
   demands, confirmed by reading each named test alongside its AC:
   - AC5.2: a timed-out attempt is recorded (`error="timed_out"`), but no test
     runs a timed-out subtask through the full item loop to assert the item
     does not advance past it.
   - AC5.6: `tests/test_run_progress.py` checks stderr-only output and
     suppression under `--json`/non-TTY, but never byte-diffs stdout with
     progress on vs. off as the AC specifically demands.
   - AC6.8: `tests/test_hollow_checks.py` checks the zero-byte-file behaviour
     but doesn't assert the literal `evidence["error"] == "file_is_empty"` key.
   - AC10.6 / AC10.8: stale-lock reclaim and remove-nonexistent-id are both
     tested at the `lock.py`/`schedule.py` unit level but have no `_cmd_run`/
     CLI-level test proving the reclaimed lock lets a real run proceed, or
     that the CLI exit code for removing an unknown id is non-zero.
   - AC-S5: `advance_failure_count`'s "not double-counted on retry" clause has
     no test reading the actual `@failed=N` value in the todo file before and
     after a `--retry-failed` resume.

   None of these are behaviour bugs as far as this pass could confirm — each
   is a test that doesn't yet say what its AC claims. Add the missing
   assertion to the existing test module in each case; if writing any of them
   surfaces an actual behavioural gap (rather than just a missing assertion),
   stop and record that as its own item instead of silently fixing it here.

   *Validation:* `make check` + `pytest tests/test_execute.py tests/test_run_progress.py tests/test_hollow_checks.py tests/test_run.py tests/test_backoff.py -q`.
   *Closes:* AC5.2, AC5.6, AC6.8, AC10.6, AC10.8, AC-S5 (test-coverage
   completion only).
   *Branch slug:* `close-thin-ac-test-coverage`

W5. **NEEDS A HUMAN DECISION before any code — halt-on-fail / run-level item
   loop.** Stage 7's prose says budget exhaustion means "the run moves to the
   next eligible item (or halts entirely under `--halt-on-fail`)" and AC7.4
   requires the same choice. But confirmed by reading `cli.py`'s `_cmd_run`
   and `_cmd_resume` in full: **a single `jumar run`/`jumar resume` invocation
   only ever selects and processes one item, then returns** — there is no
   loop over multiple eligible items within one process for `--halt-on-fail`
   to interrupt. `USAGE.md` itself documents this as intentional ("one item
   per invocation, under the single-flight lock"), matching the scheduler
   design (cron/launchd re-invoke `jumar run` per tick) and the single-flight
   lock. `config.halt_on_fail` and `RepairExhausted.halt` are wired correctly
   as far as they go (`repair.py` sets `.halt` from config, tested in
   `tests/test_repair.py`) but `.halt` is never read anywhere in `cli.py` —
   confirmed by grep — so it has no effect regardless of value, and there is
   also no `--halt-on-fail` CLI flag in `cli.py`'s argparse despite the spec
   writing it as one.

   This is not a small bug fix: closing it "as written" means adding a
   multi-item loop to a single `jumar run` invocation, which is a real
   architecture change to something `USAGE.md` currently documents as
   one-item-per-invocation by design. The alternative is that AC7.4/Stage 7's
   prose is the stale artefact (written before or independent of the
   one-item-per-invocation decision) and should be corrected to match the
   shipped design, with `config.halt_on_fail` and `RepairExhausted.halt` either
   removed as vestigial or repurposed for a future multi-item mode. Do not
   guess which; that's exactly the kind of premise P2 was closed over.

   *Validation (once decided):* if a run-level loop is chosen — `make check` +
   `pytest tests/test_run.py -q` with a fixture todo file with two eligible
   items: default behaviour exhausts item A's repair budget and still attempts
   item B; `--halt-on-fail` stops after A fails and B is never selected. If the
   spec is corrected instead — no `src/jumar/` change; a human/`update`-beat
   edit to `specs/02-functional-spec.md` Stage 7, and this item closes without
   a branch.
   *Closes:* AC7.4, once the premise is resolved.
   *Branch slug:* `resolve-halt-on-fail-semantics` (only if the multi-item
   route is chosen).

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
- **Parallel subtask execution is CLOSED, not open** (see P2 above,
  2026-08-22) — serial execution stands because `depends_on`/`write_fs` are
  model-authored claims, not enforced facts, and an undeclared write collision
  is invisible to any planning-time check. Do not re-plan it; the reopen
  condition is written up in P2 and requires an enforced per-subtask
  filesystem sandbox, not just wall-clock pain.
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
- **README: quote the wall-clock price** (from P2's closure). Users currently
  discover it by running a wide plan. State it plainly: a subtask is a full
  agentic session of eight to ten model round trips, execution is serial
  because each one is verified before the next starts, and fifteen subtasks is
  about an hour before repairs. Say what reduces it — a narrower plan, cheaper
  per-stage models on execute — and that scheduling it unattended makes the
  number moot.

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
