<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Jumar

[![CI](https://github.com/justinmclean/jumar/actions/workflows/ci.yml/badge.svg)](https://github.com/justinmclean/jumar/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Turn a plain Markdown todo list into **verified** work.

For each item on the list, Jumar breaks it into concrete subtasks, does
**one subtask at a time**, and **proves each one worked** — with a real,
executable check — before starting the next. An item is done when every one of
its subtasks passed its own check. If a check cannot be passed, the item stops
and says exactly which subtask and which check failed.

The unit of trust is the verified subtask, not the agent's claim of success.

```
todo file → ingest → select ─┬─ time eligibility (@not-before / @due / @every)
                             └─ dependency eligibility (@depends)
          → decompose → gate → ┌ execute one subtask ┐
                               │ verify it          │ ← repeat
                               └ repair (bounded)   ┘
                               → complete item → report
```

## What you get

**A run that fails loudly instead of succeeding vaguely.** Hand "migrate the
export script" to an agent and you get *something*, plus a confident summary.
Nothing checks that it works and nothing notices that steps two through five
never happened. Here the checkbox stays unticked, the report names the subtask
and the check that failed, and the exit status is non-zero. Confident,
unverified, partial completion is the failure mode the whole design exists to
refuse.

**A forensic record of what actually happened.** Every run appends to
`runs/<run-id>/journal.jsonl` — one line per event, strictly ordered, written
before the next step starts. Days later you can reconstruct which check ran,
what the evidence was, what the agent claimed versus what the verifier found,
and how each repair attempt differed. `jumar report <run-id>` renders it;
`jumar status` rolls it up per item across every run.

**Something you can leave on a schedule.** A single-flight lock means a slow run
never overlaps its successor — a second invocation exits 0 with
`already_running`, not an error. Repeated failure advances `@failed=` on the
item and parks it with `@paused=auto-failures` at the threshold, so a broken
item stops re-spending its budget every firing instead of failing nightly
forever. Recurrence is delegated to cron or launchd; there is no daemon and no
resident watcher.

**A plan before any agent runs.** `jumar plan --dry-run` decomposes the next
eligible item and prints the subtasks and their checks without executing
anything. On a vague item that breakdown is useful on its own, and it is cheap.

**Pressure on you to define "done" first.** An item the system will accept has
to say what proof looks like. A subtask with no executable acceptance check is
rejected at planning time — "trust me" is not a check — so the specification
work happens before the agent runs rather than during review. This is the least
obvious benefit and often the largest.

**Nothing leaves the machine.** The system never pushes and never opens a PR. It
stops at a local commit and prints the commands for you to run yourself.

## How the refusal is built

- A subtask with no executable acceptance check is **rejected at planning time**.
- A `command` check must be a real argv. A shell wrapper (`bash -c "…"`) is
  refused outright, and so is an argv that cannot fail — `true`, `echo`, bare
  `ls`. A check that always passes is not a check.
- A `file` check **fails on a zero-byte file**. A download that returned no body
  leaves the path present and empty, and `test -f` is happy with that.
- Verification runs in a **fresh context** that never sees the executing agent's
  reasoning — only the world it left behind.
- The judge verifier is prompted **adversarially**: its default answer is fail,
  and it must cite specific evidence to pass.
- A check that cannot run is `inconclusive`, which is **not** a pass.
- Repairs are **bounded**, and a repair may never rewrite its own check.
- A deadline changes only **queue position**. Being overdue never shortens a
  plan or skips verification — a test asserts `due` is unread by the decompose,
  execute, verify and repair paths.

## What it does not do

Checks are proposed by the same model that does the work, so they establish that
an artefact exists and has the shape that was asked for. They do not establish
that its *content* is correct. Verification is strongest where acceptance is
mechanical — a test suite, an exit status, a file that must contain a specific
value — and weakest on judgement work, where "a document exists and mentions the
right things" is the most a check can assert. On that kind of task the system
still enforces the process (sources fetched before drafting, nothing marked done
that did not run) but a human reviewer remains the one who decides whether the
reasoning is sound.

## Status

Everything below is built, tested and merged.

| Command | What it does |
|---|---|
| `jumar plan` | Ingest, select, decompose, print. `--dry-run` stops before execution. |
| `jumar run` | The full pipeline for the next eligible item. |
| `jumar resume <run-id>` | Replay the journal and continue from the first unverified subtask. |
| `jumar report <run-id>` | Render a run report. Exit 1 if anything failed. |
| `jumar status` | Item-centric view across the todo file and every run journal. |
| `jumar schedule add\|list\|remove\|show` | Install and inspect cron/launchd entries. |
| `jumar doctor` | Check config, harness binary, allow list, schedules, todo file. |

`--json` is available on `plan`, `run`, `report` and `status`; `--verbose` streams the
agent's output during `run`. Progress goes to stderr so stdout stays clean, and
is suppressed under `--json` or when stderr is not a TTY, so scheduled runs stay
quiet. CI runs `make check` with a per-module coverage floor.

Known gaps are tracked in **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**.

## Install

Python 3.11 or newer (the code uses `datetime.UTC`).

```bash
git clone https://github.com/justinmclean/jumar.git Jumar && cd Jumar
make install      # editable install + dev tools (pytest, ruff, mypy)
make check        # ruff + mypy + pytest + the build-loop fixture tests
jumar --version
```

## Quick start

```bash
cp todo.example.md todo.md      # todo.md is git-ignored by default
$EDITOR todo.md
jumar plan --dry-run              # see what it would pick, and why
jumar run --approve               # confirm each subtask before it runs
jumar status                      # where everything stands
```

Full worked examples, the flag reference, and the config reference are in
**[USAGE.md](USAGE.md)**.

## Local models

`[jumar.harness] agent = "openai"` points jumar at any OpenAI-compatible
`/chat/completions` endpoint — LM Studio, llama.cpp's server, vLLM — with no
extra dependency (stdlib `urllib.request` only). Unlike the other six
harnesses this one is not a CLI wrapper: jumar drives the tool-calling loop
itself, so `read_file` / `write_file` / `run_command` are checked against the
same `Capability` set and command allow/deny policy as everything else in the
system, in-process — no `allow_unrestricted_harness` opt-in needed. `jumar
doctor` swaps its usual PATH check for a GET on `{base_url}/models`. Keep
`[jumar.harness.judge]` on a frontier model even when `execute` runs locally —
a local model grading its own output is a weaker check than the design
assumes. Full config keys and the caveats in **[USAGE.md §8](USAGE.md#8-configuration)**.

## Todo file syntax

```markdown
Context prose above an item is passed to the agent as background,
never treated as work.

- [ ] Add a --json flag to the export script @id=export-json @priority=1 @capability=write_fs
- [ ] Update the README install section @depends=export-json
      - [ ] Rewrite the install steps for the new flag
      - [ ] Check every command in the README actually runs
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [x] Already done — skipped
```

| Token | Meaning |
|---|---|
| `@id=` | Stable item id. Otherwise derived from the text plus a hash. |
| `@priority=` | Lower sorts first. |
| `@depends=` | Blocks until the named item is done. Cycles are a startup error. |
| `@capability=` | Grants authority: `read_fs`, `write_fs`, `run_commands`, `network`, `git_commit`. |
| `@max-subtasks=` | Per-item override of the plan-length cap. |
| `@not-before=` | Eligibility gate. Before this instant the item is *deferred*. |
| `@due=` | Advisory deadline. Affects ordering and reporting only. |
| `@every=` | Recurrence: `weekday`, `1d`, `2w`, `mon,thu`. |
| `@failed=` | Consecutive failure count. Written by the system, cleared on success. |
| `@paused=` | Parked; never selected. Written by the system at the failure threshold. |

An indented task list under an item is **your** breakdown and is used verbatim;
the model is only asked to supply acceptance checks for steps that lack one.

A completed `@every=` item stays **unchecked** with its `@not-before=` advanced
to the next occurrence — that is what makes it recurring. An unparseable
schedule token blocks that item rather than being guessed at.

## Layout

| Path | What it is |
|---|---|
| `specs/` | The specification. Ten stages, ~74 numbered acceptance criteria. |
| `IMPLEMENTATION_PLAN.md` | Prioritised work items. One item = one branch = one PR. |
| `AGENTS.md` | Operational rules for any agent working in this repo. |
| `tools/spec-loop/` | The spec-driven build loop that builds this repo. |
| `src/jumar/` | The Python package. |
| `tests/` | pytest suites, one module per stage. |
| `USAGE.md` | Worked examples, command reference, config reference. |

## The spec pack

- **[01 — Product & Vision](specs/01-product-spec.md)** — problem, goals,
  non-goals, guiding principles.
- **[02 — Functional Spec](specs/02-functional-spec.md)** — the ten stages, each
  with behaviour, contract, and numbered acceptance criteria (AC1.1 … AC10.9)
  that tests check directly.
- **[03 — Data Model](specs/03-data-model.md)** — every shape, its invariants,
  and the append-only journal format.
- **[04 — Technical Plan](specs/04-technical-plan.md)** — stack, package layout,
  the harness abstraction, execution isolation, clock and scheduling rules, and
  the build phases.
- **[05 — Operator Tooling](specs/05-operator-tooling.md)** — the spec loop, and
  how the product's discipline maps onto how the repo is built.

## How the repo is built

Jumar is built the way Jumar works — one work item at a time, on
its own branch, validated before it commits:

| product concept | build-loop equivalent |
|---|---|
| todo item | work item in `IMPLEMENTATION_PLAN.md` |
| decomposition | the `plan` beat |
| one subtask at a time | one work item per `build` iteration |
| acceptance check | the item's Validation command (`make check`) |
| verification gate | no commit until validation is green |
| journal | git history — one commit per verified item |

```bash
./tools/spec-loop/loop.sh plan      # derive work items from the specs
./tools/spec-loop/loop.sh build 1   # build exactly one, on its own branch
```

Review the branch, push it yourself, open the PR yourself. The loop stops at a
local commit by design. Full detail in
[`tools/spec-loop/README.md`](tools/spec-loop/README.md).

Prefer `build 1` followed by a merge over `build 3`. Parallel iterations can't
see each other's files, so two branches will independently implement the same
shared module and you'll spend the saved time untangling it.

## Security posture

The enforced boundary is **send, not fetch**. An agent that cannot reach a
primary source writes from memory instead, which is the worse outcome.

- `network` **is** a default capability, and `curl`/`wget` are on the allow list.
- The deny list is the outbound-transmission vectors: `mail`, `mailx`,
  `sendmail`, `ssmtp`, `msmtp`, `ssh`, `scp`, `sftp`, `rsync`. `git push` and
  `gh` are hard-denied in every dispatched argv, matched on the argv basename so
  an absolute path does not slip past.
- The allow list is consulted **before** a process is spawned. A refused argv is
  `inconclusive` and never runs.
- Every subprocess is argv with `shell=False`, and `models.Check` refuses
  `bash -c` wrappers so the list cannot be sidestepped with one array element.
- Secrets come from the environment or a git-ignored `.env`, never the repo.

With `python3` on the allow list and the network reachable, none of the above
stops a determined agent — it is defence in depth, not a sandbox. The real
control is the execution environment: run jumar inside a container or VM with
restricted egress and no push credentials. The agent CLI runs with its
unattended flag, which bypasses the *agent's* permission prompts, not the OS.

## Contributing

Hand-written PRs are welcome — the spec-loop is how this repo is usually
built, not a requirement for contributing. Start with
[CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through
[SECURITY.md](SECURITY.md), not the public issue tracker.

## Licence

Apache-2.0.
