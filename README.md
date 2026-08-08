<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# GetStuffDone

Turn a plain Markdown todo list into **verified** work.

For each item on the list, GetStuffDone breaks it into concrete subtasks, does
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

## Why this exists

Handing "migrate the export script" to an agent produces *something*. Nothing
checks that it works, and nothing notices that steps two through five were never
done. The failure mode is confident, unverified, partial completion — so the
whole design is built around refusing to advance on unproven work:

- A subtask with no executable acceptance check is **rejected at planning time**.
  "Trust me" is not a check.
- Verification runs in a **fresh context** that never sees the executing agent's
  reasoning — only the world it left behind.
- The judge verifier is prompted **adversarially**: its default answer is fail,
  and it must cite specific evidence to pass.
- A check that cannot run is `inconclusive`, which is **not** a pass.
- Repairs are **bounded**, and a repair may never rewrite its own check.
- A deadline changes only **queue position**. Being overdue never shortens a
  plan or skips verification — a test asserts `due` is unread by the decompose,
  execute, verify and repair paths.
- The system **never pushes and never opens a PR**. It stops at a local commit.

## Status

Honest state of play, because a tool about verification should not overstate
itself:

| Command | State |
|---|---|
| `gsd plan --dry-run` | **Works.** Ingests, selects, prints the eligible item plus deferred/blocked. No agent calls. |
| `gsd report <run-id>` | **Works.** Builds `report.md` from a run journal; exit 1 if anything failed. |
| `gsd resume <run-id>` | **Works.** Replays the journal and runs the full execute→verify→complete loop from the first unverified subtask. |
| `gsd run` | **Stub** — exits 2. Flag validation is live, the pipeline is not yet wired. |
| `gsd schedule`, `gsd doctor` | **Stubs** — exit 2. |

Every pipeline *module* is built and tested: `ingest`, `select`, `clock`,
`recurrence`, `decompose`, `gate`, `execute`, `repair`, `complete`, `report`,
`journal`, `harness`, and all five verifiers (`command`, `file`, `absence`,
`judge`, `manual`). What's missing is the wiring for `gsd run`, plus `lock.py`,
`schedule.py`, `doctor`, and CI. See `IMPLEMENTATION_PLAN.md`.

Because `gsd resume` already orchestrates the whole loop, the fastest path to a
working `gsd run` is to lift that orchestration into a shared function — see
**Known gaps** below.

## Install

Python 3.11 or newer (the code uses `datetime.UTC`).

```bash
git clone <your-remote> GetStuffDone && cd GetStuffDone
make install      # editable install + dev tools (pytest, ruff, mypy)
make check        # ruff + mypy + pytest + the build-loop fixture tests
gsd --version
```

## Quick start

```bash
cp todo.example.md todo.md      # todo.md is git-ignored by default
$EDITOR todo.md
gsd plan --dry-run              # see what it would pick, and why
```

Full worked examples, the flag reference, and the config reference are in
**[USAGE.md](USAGE.md)**.

## Todo file syntax

```markdown
Context prose above an item is passed to the agent as background,
never treated as work.

- [ ] Add a --json flag to the export script @priority=1 @capability=write_fs
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
| `src/getstuffdone/` | The Python package. |
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

GetStuffDone is built the way GetStuffDone works — one work item at a time, on
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

- The agent CLI runs with its unattended flag — that bypasses the **agent**
  permission layer, not an OS sandbox. Run it inside a sandbox with no push
  credentials in the environment.
- `network` is **not** a default capability; `curl`, `wget`, `ssh`, `scp` are on
  a standing deny list; `git push` and `gh` are hard-denied in every dispatched
  argv.
- Every subprocess is argv with `shell=False`. No shell-string interpolation.
- Secrets come from the environment or a git-ignored `.env`, never the repo.

## Known gaps

- **`gsd run` is not wired**, and there is no work item for it in
  `IMPLEMENTATION_PLAN.md` — the plan jumps from the verifiers to `lock.py`.
  `_cmd_resume` in `cli.py` already contains the full orchestration; the fix is
  to extract it into a shared `run_item()` that both `run` and `resume` call,
  rather than writing it twice.
- `lock.py` (single-flight) and `schedule.py` (cron/launchd) are specified in
  `specs/02-functional-spec.md` §Stage 10 but not built. Until the lock exists,
  do not install a recurring run.
- `gsd doctor` and CI are unbuilt.
- The build loop has no single-flight lock of its own, so a scheduled loop must
  not overlap its own run window.

## Licence

Apache-2.0.
