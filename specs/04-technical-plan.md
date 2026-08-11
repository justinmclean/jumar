<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->
---
status: proposed
---

# 04 — Technical Plan

## Stack

- **Python 3.11+**, standard library first. Third-party dependencies are
  justified per addition, not assumed.
- **pytest** for tests, **ruff** for lint/format, **mypy** for types.
- **Bash + git** for the spec loop that builds this repo (`tools/spec-loop/`).
- No database. Durable state is `runs/<run-id>/journal.jsonl` plus artefact
  files; the todo file itself stays the human-facing source of work.

## Package layout

```
src/jumar/
  __init__.py
  cli.py            # `jumar` entry point: run / plan / resume / report / schedule / doctor
  config.py         # jumar.toml + CLI flag resolution, capability + argv policy
  models.py         # every shape in 03-data-model.md, with invariants
  ingest.py         # stage 1
  select.py         # stage 2 (time + dependency eligibility)
  clock.py          # the run's single `now`, tz resolution, injectable for tests
  recurrence.py     # @every parsing + next-occurrence arithmetic
  decompose.py      # stage 3
  gate.py           # stage 4
  execute.py        # stage 5
  verify/
    __init__.py     # registry: kind -> verifier
    command.py      # stage 6, kind=command
    file.py         #          kind=file / absence
    judge.py        #          kind=judge  (adversarial, fresh context)
    manual.py       #          kind=manual
  repair.py         # stage 7
  complete.py       # stage 8 (checkbox flip, optional per-item commit)
  report.py         # stage 9
  schedule.py       # stage 10: cron/launchd/systemd install, list, remove
  lock.py           # single-flight run lock, stale-lock reclaim
  journal.py        # append-only journal + replay/resume
  harness.py        # agent CLI abstraction (see below)
tests/              # one module per stage, plus fixtures/ and golden/
```

**Single source of truth.** Each stage owns its logic; no stage reimplements
another's. New behaviour extends the owning module rather than adding a parallel
one.

## The harness abstraction

`harness.py` is the only place that knows how to invoke an agent. It exposes:

```python
def run_agent(prompt: str, *, cwd: Path, capabilities: set[Capability],
              timeout_s: int, harness: HarnessInfo) -> AgentResult
```

Deliberately mirroring `tools/spec-loop/lib.sh`, it supports `claude`, `codex`,
`cursor`, `gemini`, `opencode` and `kiro`, selecting flags by harness name. Two
consequences worth naming:

1. **Argv construction is pure and unit-tested** without launching an agent —
   the same fixture-test trick the spec loop uses (`tests/test_harness_argv.py`
   substitutes a fake agent that records its argv).
2. **Capability enforcement happens here**, at the single choke point: the
   allow/deny argv[0] policy and the network/git flags are applied when the
   command line is built, so no stage can accidentally bypass them.

Structured output (plans, judge verdicts) is requested as JSON and parsed
defensively: a response that does not validate against the model is a rejection,
never a partial accept.

## Execution isolation

- Subtasks run with `cwd` set to the item's working directory (default: repo
  root) and inherit a **scrubbed environment**: no `GITHUB_TOKEN`, no
  `*_API_KEY` beyond the one the harness itself needs, no SSH agent socket.
  The Claude harness argv also carries `--strict-mcp-config`, so
  user-configured MCP servers (e.g. a mail server) are not inherited into the
  session and cannot bypass the send-boundary deny list below.
- The enforced boundary is **send, not fetch** (decided 2026-08-08). `network`
  **is** a default capability and `curl`/`wget` are on the allow list — an
  agent that cannot reach a primary source fabricates it instead, the worse
  failure. The standing deny list is the outbound-transmission vectors:
  `mail`, `mailx`, `sendmail`, `ssmtp`, `msmtp`, `ssh`, `scp`, `sftp`,
  `rsync`. The allow list is defence in depth, not a sandbox — with `python3`
  allowed and the network reachable, the real control is running jumar in a
  container/VM with restricted egress and no push credentials.
- `git push` and `gh` are **hard-denied** in every dispatched argv, mirroring the
  spec loop's `--disallowedTools` defence in depth. AC8.4 tests this.
- Command checks use `subprocess.run` with an argv list, `shell=False`, a
  timeout, and captured output. No shell string interpolation, anywhere.

## Verifier design

The registry maps `CheckKind → Verifier` protocol
(`def verify(check: Check, ctx: VerifyContext) -> VerificationResult`). Adding a
kind is: write the module, register it, add a test (AC6.7 asserts a dummy kind
can be registered end to end without touching `execute.py`).

The `judge` verifier is the delicate one. Its contract:

- It is given the acceptance `statement`, the artefacts (file contents, diffs,
  command outputs) and **nothing from the executing agent's transcript**.
- Its prompt frames the default answer as **fail**: it must cite specific
  evidence to return `pass`, and "looks reasonable" is explicitly not evidence.
- It returns structured `{verdict, reason, artefacts_shown}`; an unparseable
  response is `inconclusive`, not a pass.

## Time, clocks, and scheduling

Two independent mechanisms, deliberately not conflated:

**Item eligibility** (`clock.py`, `recurrence.py`, consumed by `select.py`) —
*which* items may be worked. **Run scheduling** (`schedule.py`) — *when the
process starts at all*. The first is in-process and unit-testable; the second is
delegated to the OS.

Rules that make the time logic testable and safe:

- **One clock, injected.** Nothing calls `datetime.now()` outside `clock.py`.
  The run captures `now` once, journals it, and passes it down. A test asserts no
  other module imports a wall-clock call — the same discipline the spec loop
  needs for reproducible replay, for the same reason.
- **UTC internally, local at the edges.** Bare dates are resolved in the
  configured IANA zone at parse time; everything downstream is timezone-aware
  UTC. Naive datetimes never enter the model.
- **DST is a parsing concern, not an arithmetic one.** Recurrence arithmetic
  operates on local wall-clock intent ("every weekday at 09:00") and re-resolves
  to UTC per occurrence, so a daily item does not drift an hour twice a year.
  Non-existent and ambiguous local times (the DST gap and fold) resolve
  forward, deterministically, and the choice is tested rather than inherited
  from whatever the platform does.
- **No catch-up storms.** A missed recurrence advances to the next occurrence
  after `now`, never a backlog (AC8.6). Executed work is not a counter.
- **Single-flight.** `lock.py` guards a todo path with a PID-stamped lock file so
  two cron firings cannot run agents over the same tree concurrently. Stale
  locks are reclaimed and the reclaim is journalled — a lock that can only be
  cleared by hand turns one crash into a silently dead schedule.
- **Scheduled runs are non-interactive by construction**, so `manual` checks are
  `inconclusive` rather than a hung headless process, and `--approve` is refused
  at startup (AC4.4).
- **`due` is inert outside ordering and reporting.** A test asserts that
  `decompose`, `execute`, `verify`, and `repair` never read it. Deadline
  pressure must not be able to reach the verification path — that is the one
  coupling that would quietly turn this into the thing it was built to replace.

The scheduler backends write into files the user also owns (a crontab, a
LaunchAgents plist), so every write is delimited by `jumar <schedule-id>` markers
and `remove` only ever edits between its own markers. Unrecognised lines are
never reformatted.

## Ordering and concurrency

v1 is strictly sequential: one item, one subtask, one verification at a time.
The journal invariant (`attempt_finished` → `verification` before the next
`attempt_started`) is what makes that testable. Intra-item parallelism across
independent `depends_on` branches is a **later** phase and must not weaken that
invariant — it would be reformulated per dependency chain, not dropped.

## Phases (the order the loop should build in)

- **Phase 0 — skeleton.** `models.py` with invariants, `config.py`,
  `journal.py`, `cli.py` shell, test scaffolding, `make check` green.
- **Phase 1 — read-only path.** `ingest.py`, `select.py`, `jumar plan --dry-run`
  printing the parsed items. No agent calls yet.
- **Phase 2 — decompose + gate.** `harness.py`, `decompose.py`, `gate.py`;
  `jumar plan` produces and journals a validated plan. Still executes nothing.
- **Phase 3 — the inner loop.** `execute.py`, `verify/command.py`,
  `verify/file.py`, `repair.py`. This is the first phase that can do work, and
  the first that can be trusted, because verification lands with execution —
  never before it in the build order.
- **Phase 4 — completion.** `complete.py` (checkbox flip, per-item branch
  commit), `report.py`, `jumar resume`.
- **Phase 4b — item scheduling.** `clock.py`, `recurrence.py`, the eligibility
  gate in `select.py`, and recurrence advance in `complete.py`. Lands *after*
  completion because recurrence rewrites the same line the checkbox flip does,
  and *before* run scheduling because an unattended run with no eligibility gate
  would just redo everything nightly.
- **Phase 5 — the remaining verifiers.** `verify/judge.py`,
  `verify/absence.py`, `verify/manual.py`.
- **Phase 5b — run scheduling.** `lock.py`, then `schedule.py` with the cron
  backend, then launchd/systemd. The lock ships first: installing a recurring
  run before single-flight exists is how you get two agents editing one tree.
- **Phase 6 — polish.** `jumar doctor` (config + harness + allow-list + installed
  schedule sanity), richer reports, evidence artefact pruning.

**Sequencing rule:** no phase that executes work may land before the
verification it depends on. Phase 3 ships `execute` and `verify` together, in
one work item or two strictly-ordered ones.

## Decisions

- **Markdown todo file, not a database.** The list must stay editable by hand in
  any editor; state that belongs to the tool lives in `runs/`, never in the
  user's file beyond the checkbox flip.
- **Journal before side effect.** Every stage writes its journal entry before
  the action it describes completes, so a crash leaves the record ahead of
  reality rather than behind it.
- **Verification in a fresh context.** Reusing the executor's context would make
  the check a self-assessment, which is precisely what the product exists to
  avoid.
- **No push, no PR, ever.** The system stops at a local commit and prints the
  commands. Same rule as the spec loop, for the same reason.
- **Bounded repairs over unbounded retry.** An agent that cannot pass a check in
  three tries is not converging; more attempts mostly produce more damage.
- **Scheduling is delegated to the OS.** Writing a scheduler means reimplementing
  wake-from-sleep, missed-fire policy, boot ordering, and user sessions — all of
  which cron/launchd/systemd already do better. Jumar writes an entry and
  exits.
- **Eligibility lives in the todo line, not a sidecar file.** `@not-before=` and
  `@every=` stay visible where the human edits them; a separate schedule store
  would drift from the list it describes.
- **The run path is identical whether a human or cron started it.** `trigger` is
  recorded for the journal, but no behaviour branches on it except interactivity
  — one code path is one set of bugs.
- **Deferred:** parallel subtasks, cross-item planning, non-Markdown inputs,
  external task-tracker sync, a resident daemon, Windows Task Scheduler,
  calendar/ICS import, notification delivery on failure.

## Testing strategy

- One pytest module per stage, named for the stage.
- **Fake harness** fixture: a deterministic agent stub returning canned plans
  and claims, so stages 3–7 are tested without a live model.
- **Golden journals**: fixture runs whose `journal.jsonl` is asserted whole,
  which is how the ordering invariants stay honest.
- **Negative tests are first-class**: unverifiable plans, self-modified checks,
  ungranted capabilities, missing verifier binaries, corrupt journals,
  unparseable schedule tokens, concurrent runs. The product is a refusal
  machine; most of its value is in the paths where it says no.
- **Time is always injected, never real.** Every scheduling test runs against a
  frozen or scripted clock and a fixed timezone, including explicit DST-gap and
  DST-fold cases. A test that reads the real clock is a flaky test waiting for
  the last Sunday in October.
- **Scheduler backends are tested against a fake backend** that records what
  would be written, plus a round-trip test over a fixture crontab containing
  unrelated user entries, asserting they survive install and remove
  byte-identical.
- Coverage floor enforced in CI once Phase 1 lands; every module ≥ 90%.
